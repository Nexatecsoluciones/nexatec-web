"""Resolucion de tenant+sistema+entorno a partir del Host header.

Unico punto de entrada para traducir un hostname publico (subdominio de
nexatecpy.com) en un tenant. Deliberadamente estricto: cualquier valor que
no sea un hostname DNS valido, o que este en la lista de reservados
(sitio publico, admin, api interna, etc.), nunca llega a tocar la tabla
`tenant_hostnames` -- devuelve None, y el router que llama a esto responde
404 generico, nunca un error que distinga "hostname invalido" de "hostname
valido pero sin tenant" (evita enumeracion de subdominios).
"""

import re
import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenancy import TenantHostname
from app.models.tenancy_enums import Environment

# Subdominios y hosts que jamas pueden resolver a un tenant, aunque alguien
# los inserte a mano en la tabla (ver register_tenant_hostname). Incluye el
# apex y variantes que ya sirven o van a servir el sitio/plataforma propia
# de NEXATEC, nunca a un cliente.
RESERVED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "nexatecpy.com",
        "www.nexatecpy.com",
        "admin.nexatecpy.com",
        "api.nexatecpy.com",
        "staging.nexatecpy.com",
        "static.nexatecpy.com",
        "portal.nexatecpy.com",
        "app.nexatecpy.com",
        "mail.nexatecpy.com",
        "localhost",
        "127.0.0.1",
    }
)

_MAX_HOSTNAME_LENGTH = 253
# Un label DNS valido: letras/digitos, guiones internos, 1-63 caracteres,
# sin empezar/terminar en guion. El hostname completo es 1+ labels unidos
# por puntos.
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(\.{_LABEL})+$")


def normalize_host(raw_host: str | None) -> str | None:
    """Saca el puerto si viene incluido, pasa a minusculas y valida forma
    de hostname DNS. Devuelve None ante cualquier cosa que no sea un
    hostname bien formado (headers spoofeados, paths, SQL/XSS, binario,
    strings larguisimos, etc.) -- nunca lanza excepcion."""
    if not raw_host or not isinstance(raw_host, str):
        return None

    host = raw_host.strip()
    if not host or len(host) > _MAX_HOSTNAME_LENGTH:
        return None

    # IPv6 con puerto viene como "[::1]:4302" -- no es un caso real para
    # hostnames de tenant, se descarta directo.
    if host.startswith("["):
        return None

    # Host header estandar: "ejemplo.com:4302" -- el puerto es siempre el
    # ultimo segmento tras el unico ":" permitido.
    if host.count(":") > 1:
        return None
    if ":" in host:
        host, _, port = host.partition(":")
        if not port.isdigit():
            return None

    host = host.lower()
    if not _HOSTNAME_RE.match(host):
        return None

    return host


def resolve_hostname(db: Session, raw_host: str | None) -> TenantHostname | None:
    """Devuelve el TenantHostname activo para este Host header, o None si
    el host es invalido, esta reservado, o no esta registrado."""
    host = normalize_host(raw_host)
    if host is None or host in RESERVED_HOSTNAMES:
        return None

    return db.execute(
        select(TenantHostname).where(TenantHostname.hostname == host)
    ).scalar_one_or_none()


def public_host_from_request(request: Request) -> str | None:
    """El hostname publico real que el navegador uso para esta request.

    `X-Forwarded-Host` es seteado por el BFF same-origin de Next.js
    (apps/web/src/app/api/[...path]/route.ts) a partir del `Host` que
    llego de Cloudflare -- es el UNICO camino de produccion. Solo se cae a
    `Host` directo (lo que ve uvicorn) para desarrollo local y tests que
    le pegan a la API sin pasar por el BFF. uvicorn corre con
    `--forwarded-allow-ips=127.0.0.1` y 4301 solo escucha en loopback, asi
    que ninguno de estos dos headers puede llegar spoofeado desde
    internet -- igual se sanean los dos exactamente igual en
    `normalize_host`, por las dudas."""
    return request.headers.get("x-forwarded-host") or request.headers.get("host")


def resolve_request_hostname(db: Session, request: Request) -> TenantHostname | None:
    """Atajo para usar como dependency de FastAPI: resuelve el tenant de
    la request actual a partir de su hostname publico real."""
    return resolve_hostname(db, public_host_from_request(request))


def register_tenant_hostname(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    system_id: uuid.UUID,
    environment: Environment,
    hostname: str,
    is_primary: bool = False,
) -> TenantHostname:
    """Registra un hostname nuevo para un tenant+sistema+entorno. Valida
    forma y reservados server-side (nunca confia en que el caller ya
    valido). Lanza ValueError si el hostname es invalido, esta reservado,
    o ya esta tomado -- el caller (job de provisioning, endpoint admin)
    decide como reportar eso, esta funcion no hace HTTP."""
    normalized = normalize_host(hostname)
    if normalized is None:
        raise ValueError(f"Hostname invalido: {hostname!r}")
    if normalized in RESERVED_HOSTNAMES:
        raise ValueError(f"Hostname reservado, no se puede asignar a un tenant: {normalized}")

    existing = db.execute(
        select(TenantHostname).where(TenantHostname.hostname == normalized)
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Hostname ya registrado: {normalized}")

    record = TenantHostname(
        tenant_id=tenant_id,
        system_id=system_id,
        environment=environment,
        hostname=normalized,
        is_primary=is_primary,
    )
    db.add(record)
    db.flush()
    return record
