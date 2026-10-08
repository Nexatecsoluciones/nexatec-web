"""Expone un hostname de tenant (ver app/services/hostname_resolution.py)
en Cloudflare de verdad: registro DNS CNAME + ruta en el Tunnel
`nexatec-platform`. Sin esto, un hostname en `tenant_hostnames` es solo un
dato guardado -- nadie en internet puede llegar a el (ver
docs/TENANT_HOSTNAME_ROUTING.md).

Mismo patron que app/services/payments/bancard.py: sin credenciales
configuradas, no hace nada y nunca bloquea al caller (ver
settings.cloudflare_configured) -- CLOUDFLARE_API_TOKEN es un API Token
acotado (Zone.DNS:Edit + Account.Cloudflare Tunnel:Edit sobre esta
zona/cuenta nada mas), nunca la Global API Key.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from sqlalchemy import text

from app.core.config import get_settings

_API_BASE = "https://api.cloudflare.com/client/v4"
_TIMEOUT = 10.0

# Clave fija de pg_advisory_lock para serializar el read-modify-write del
# ingress del tunnel. La API (uvicorn) y el timer de barrido de demos son
# procesos distintos -- un lock en memoria no alcanza, Postgres si.
_TUNNEL_INGRESS_LOCK_KEY = 7_412_903_551


@contextmanager
def _tunnel_ingress_lock() -> Iterator[None]:
    from app.core.db import engine

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _TUNNEL_INGRESS_LOCK_KEY})
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _TUNNEL_INGRESS_LOCK_KEY})


class CloudflareNotConfiguredError(Exception):
    """No hay API Token de Cloudflare configurado todavia."""


class CloudflareApiError(Exception):
    def __init__(self, message: str):
        super().__init__(message)


def _client() -> httpx.Client:
    settings = get_settings()
    if not settings.cloudflare_configured:
        raise CloudflareNotConfiguredError(
            "Cloudflare no esta configurado todavia (falta CLOUDFLARE_API_TOKEN/"
            "CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_ZONE_ID/CLOUDFLARE_TUNNEL_ID)."
        )
    return httpx.Client(
        base_url=_API_BASE,
        headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
        timeout=_TIMEOUT,
    )


def _raise_if_failed(resp: httpx.Response, action: str) -> dict:
    data = resp.json()
    if not data.get("success"):
        # Nunca se loggea el token (no esta en el body de respuesta de
        # Cloudflare), pero si el detalle del error para poder diagnosticar.
        errors = data.get("errors") or [{"message": resp.text[:300]}]
        raise CloudflareApiError(f"{action} fallo: {errors}")
    return data


def _ensure_dns_record(client: httpx.Client, zone_id: str, hostname: str, target: str) -> None:
    existing = _raise_if_failed(
        client.get(f"/zones/{zone_id}/dns_records", params={"type": "CNAME", "name": hostname}),
        "listar DNS records",
    )
    records = existing["result"]
    if records:
        record = records[0]
        if record["content"] == target and record.get("proxied") is True:
            return  # ya esta bien, nada que hacer
        raise CloudflareApiError(
            f"Ya existe un registro CNAME para {hostname} apuntando a "
            f"{record['content']!r} (proxied={record.get('proxied')}), distinto del "
            f"esperado ({target!r}, proxied=True). No se sobreescribe automaticamente."
        )

    _raise_if_failed(
        client.post(
            f"/zones/{zone_id}/dns_records",
            json={"type": "CNAME", "name": hostname, "content": target, "proxied": True, "ttl": 1},
        ),
        f"crear DNS record para {hostname}",
    )


def _ensure_tunnel_ingress_rule(client: httpx.Client, account_id: str, tunnel_id: str, hostname: str, service: str) -> None:
    current = _raise_if_failed(
        client.get(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"),
        "leer configuracion del tunnel",
    )
    config = (current["result"] or {}).get("config") or {"ingress": [{"service": "http_status:404"}]}
    ingress = list(config.get("ingress") or [{"service": "http_status:404"}])

    if any(rule.get("hostname") == hostname for rule in ingress):
        return  # ya tiene una ruta -- no se duplica ni se pisa

    # La regla catch-all (sin "hostname") tiene que quedar siempre
    # ultima, es la que hace que cualquier host no listado de 404 en vez
    # de caer silenciosamente en el primer servicio de la lista.
    catch_all = [r for r in ingress if "hostname" not in r]
    with_hostname = [r for r in ingress if "hostname" in r]
    new_ingress = with_hostname + [{"hostname": hostname, "service": service}] + catch_all

    config["ingress"] = new_ingress
    _raise_if_failed(
        client.put(
            f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            json={"config": config},
        ),
        f"agregar ruta de tunnel para {hostname}",
    )


def _remove_dns_record(client: httpx.Client, zone_id: str, hostname: str, target: str) -> None:
    existing = _raise_if_failed(
        client.get(f"/zones/{zone_id}/dns_records", params={"type": "CNAME", "name": hostname}),
        "listar DNS records",
    )
    records = existing["result"]
    if not records:
        return
    record = records[0]
    if record["content"] != target:
        # No es el registro que nosotros creamos -- no se toca.
        raise CloudflareApiError(
            f"El CNAME de {hostname} apunta a {record['content']!r}, no a {target!r}; "
            "no se borra automaticamente algo que no sabemos que creamos nosotros."
        )
    _raise_if_failed(client.delete(f"/zones/{zone_id}/dns_records/{record['id']}"), f"borrar DNS record de {hostname}")


def _remove_tunnel_ingress_rule(client: httpx.Client, account_id: str, tunnel_id: str, hostname: str) -> None:
    current = _raise_if_failed(
        client.get(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"),
        "leer configuracion del tunnel",
    )
    config = (current["result"] or {}).get("config") or {"ingress": [{"service": "http_status:404"}]}
    ingress = list(config.get("ingress") or [])
    new_ingress = [r for r in ingress if r.get("hostname") != hostname]
    if new_ingress == ingress:
        return

    config["ingress"] = new_ingress
    _raise_if_failed(
        client.put(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations", json={"config": config}),
        f"quitar ruta de tunnel de {hostname}",
    )


def remove_public_hostname_route(hostname: str) -> None:
    """Reverso de ensure_public_hostname_route: solo borra el DNS record
    si su contenido coincide exactamente con el que nosotros habriamos
    creado (target del tunnel actual) -- nunca borra un CNAME que apunte
    a otro lado, por las dudas de que alguien lo haya reapuntado a mano."""
    settings = get_settings()
    target = f"{settings.cloudflare_tunnel_id}.cfargotunnel.com"
    with _client() as client:
        _remove_dns_record(client, settings.cloudflare_zone_id, hostname, target)
        with _tunnel_ingress_lock():
            _remove_tunnel_ingress_rule(client, settings.cloudflare_account_id, settings.cloudflare_tunnel_id, hostname)


def ensure_public_hostname_route(hostname: str) -> None:
    """Idempotente: si el hostname ya esta expuesto (DNS + ruta de tunnel
    con los valores esperados), no hace ningun cambio. Si existe algo
    distinto (otro registro DNS, por ejemplo), lanza CloudflareApiError en
    vez de pisarlo -- el caller decide si eso bloquea o no (ver
    _assign_hostname_best_effort en app/routers/demos.py, que lo trata
    como best-effort)."""
    settings = get_settings()
    target = f"{settings.cloudflare_tunnel_id}.cfargotunnel.com"
    with _client() as client:
        _ensure_dns_record(client, settings.cloudflare_zone_id, hostname, target)
        with _tunnel_ingress_lock():
            _ensure_tunnel_ingress_rule(
                client, settings.cloudflare_account_id, settings.cloudflare_tunnel_id,
                hostname, settings.cloudflare_tunnel_service,
            )
