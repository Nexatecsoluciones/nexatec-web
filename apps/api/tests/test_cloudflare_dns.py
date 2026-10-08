"""app/services/cloudflare_dns.py no habla con Cloudflare de verdad en
tests -- se intercepta httpx via MockTransport (sin dependencias nuevas)
y se mockea Settings. Cubre: sin configurar no hace nada (best-effort),
idempotencia (no vuelve a crear lo que ya esta bien), conflicto con un
DNS record existente distinto (no lo pisa), y que la regla catch-all del
Tunnel siempre queda ultima."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import cloudflare_dns


def _fake_settings(**overrides):
    base = dict(
        cloudflare_configured=True,
        cloudflare_api_token="fake-token",
        cloudflare_account_id="acc-123",
        cloudflare_zone_id="zone-123",
        cloudflare_tunnel_id="tunnel-123",
        cloudflare_tunnel_service="http://127.0.0.1:4302",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Recorder:
    """Handler de httpx.MockTransport que graba cada request y devuelve
    respuestas canned segun metodo+path, en orden."""

    def __init__(self, responses: dict[tuple[str, str], httpx.Response]):
        self.responses = responses
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, request.url.path)
        if key not in self.responses:
            raise AssertionError(f"Request inesperada: {key}")
        return self.responses[key]


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _ok(result) -> dict:
    return {"success": True, "errors": [], "messages": [], "result": result}


def test_not_configured_raises_without_any_network_call(monkeypatch):
    monkeypatch.setattr(cloudflare_dns, "get_settings", lambda: _fake_settings(cloudflare_configured=False))
    with pytest.raises(cloudflare_dns.CloudflareNotConfiguredError):
        cloudflare_dns.ensure_public_hostname_route("demo-foo.nexatecpy.com")


def test_creates_dns_record_and_ingress_rule_when_missing(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(cloudflare_dns, "get_settings", lambda: settings)

    recorder = _Recorder({
        ("GET", "/client/v4/zones/zone-123/dns_records"): _json_response(_ok([])),
        ("POST", "/client/v4/zones/zone-123/dns_records"): _json_response(_ok({"id": "dns-1"})),
        ("GET", "/client/v4/accounts/acc-123/cfd_tunnel/tunnel-123/configurations"): _json_response(
            _ok({"config": {"ingress": [
                {"hostname": "staging.nexatecpy.com", "service": "http://127.0.0.1:4302"},
                {"service": "http_status:404"},
            ]}})
        ),
        ("PUT", "/client/v4/accounts/acc-123/cfd_tunnel/tunnel-123/configurations"): _json_response(_ok({})),
    })
    monkeypatch.setattr(
        cloudflare_dns, "_client",
        lambda: httpx.Client(base_url=cloudflare_dns._API_BASE, transport=httpx.MockTransport(recorder)),
    )

    cloudflare_dns.ensure_public_hostname_route("demo-foo.nexatecpy.com")

    methods = [c.method for c in recorder.calls]
    assert methods == ["GET", "POST", "GET", "PUT"]

    post_body = json.loads(recorder.calls[1].content)
    assert post_body == {
        "type": "CNAME", "name": "demo-foo.nexatecpy.com",
        "content": "tunnel-123.cfargotunnel.com", "proxied": True, "ttl": 1,
    }

    put_body = json.loads(recorder.calls[3].content)
    ingress = put_body["config"]["ingress"]
    # La regla nueva va antes del catch-all; el catch-all (sin "hostname")
    # siempre queda al final.
    assert ingress[-1] == {"service": "http_status:404"}
    assert {"hostname": "demo-foo.nexatecpy.com", "service": "http://127.0.0.1:4302"} in ingress
    assert ingress.index({"hostname": "demo-foo.nexatecpy.com", "service": "http://127.0.0.1:4302"}) < len(ingress) - 1


def test_idempotent_when_dns_and_ingress_already_correct(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(cloudflare_dns, "get_settings", lambda: settings)

    recorder = _Recorder({
        ("GET", "/client/v4/zones/zone-123/dns_records"): _json_response(
            _ok([{"content": "tunnel-123.cfargotunnel.com", "proxied": True}])
        ),
        ("GET", "/client/v4/accounts/acc-123/cfd_tunnel/tunnel-123/configurations"): _json_response(
            _ok({"config": {"ingress": [
                {"hostname": "demo-foo.nexatecpy.com", "service": "http://127.0.0.1:4302"},
                {"service": "http_status:404"},
            ]}})
        ),
    })
    monkeypatch.setattr(
        cloudflare_dns, "_client",
        lambda: httpx.Client(base_url=cloudflare_dns._API_BASE, transport=httpx.MockTransport(recorder)),
    )

    cloudflare_dns.ensure_public_hostname_route("demo-foo.nexatecpy.com")

    # Nunca llega a crear/pisar nada -- solo los dos GET de chequeo.
    methods = [c.method for c in recorder.calls]
    assert methods == ["GET", "GET"]


def test_raises_on_conflicting_existing_dns_record(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(cloudflare_dns, "get_settings", lambda: settings)

    recorder = _Recorder({
        ("GET", "/client/v4/zones/zone-123/dns_records"): _json_response(
            _ok([{"content": "otro-destino-completamente-distinto.com", "proxied": False}])
        ),
    })
    monkeypatch.setattr(
        cloudflare_dns, "_client",
        lambda: httpx.Client(base_url=cloudflare_dns._API_BASE, transport=httpx.MockTransport(recorder)),
    )

    with pytest.raises(cloudflare_dns.CloudflareApiError):
        cloudflare_dns.ensure_public_hostname_route("demo-foo.nexatecpy.com")

    # Nunca intenta la ruta del tunnel si el DNS ya esta en conflicto.
    assert [c.method for c in recorder.calls] == ["GET"]


def test_raises_cloudflare_api_error_on_api_failure(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(cloudflare_dns, "get_settings", lambda: settings)

    recorder = _Recorder({
        ("GET", "/client/v4/zones/zone-123/dns_records"): _json_response(
            {"success": False, "errors": [{"message": "invalid token"}], "result": None}, status_code=200
        ),
    })
    monkeypatch.setattr(
        cloudflare_dns, "_client",
        lambda: httpx.Client(base_url=cloudflare_dns._API_BASE, transport=httpx.MockTransport(recorder)),
    )

    with pytest.raises(cloudflare_dns.CloudflareApiError):
        cloudflare_dns.ensure_public_hostname_route("demo-foo.nexatecpy.com")


def test_remove_deletes_dns_and_ingress_when_content_matches(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(cloudflare_dns, "get_settings", lambda: settings)

    recorder = _Recorder({
        ("GET", "/client/v4/zones/zone-123/dns_records"): _json_response(
            _ok([{"id": "dns-1", "content": "tunnel-123.cfargotunnel.com"}])
        ),
        ("DELETE", "/client/v4/zones/zone-123/dns_records/dns-1"): _json_response(_ok({})),
        ("GET", "/client/v4/accounts/acc-123/cfd_tunnel/tunnel-123/configurations"): _json_response(
            _ok({"config": {"ingress": [
                {"hostname": "demo-foo.nexatecpy.com", "service": "http://127.0.0.1:4302"},
                {"service": "http_status:404"},
            ]}})
        ),
        ("PUT", "/client/v4/accounts/acc-123/cfd_tunnel/tunnel-123/configurations"): _json_response(_ok({})),
    })
    monkeypatch.setattr(
        cloudflare_dns, "_client",
        lambda: httpx.Client(base_url=cloudflare_dns._API_BASE, transport=httpx.MockTransport(recorder)),
    )

    cloudflare_dns.remove_public_hostname_route("demo-foo.nexatecpy.com")

    assert [c.method for c in recorder.calls] == ["GET", "DELETE", "GET", "PUT"]
    put_body = json.loads(recorder.calls[3].content)
    assert put_body["config"]["ingress"] == [{"service": "http_status:404"}]


def test_remove_refuses_to_delete_dns_record_pointing_elsewhere(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(cloudflare_dns, "get_settings", lambda: settings)

    recorder = _Recorder({
        ("GET", "/client/v4/zones/zone-123/dns_records"): _json_response(
            _ok([{"id": "dns-1", "content": "algo-que-no-creamos-nosotros.com"}])
        ),
    })
    monkeypatch.setattr(
        cloudflare_dns, "_client",
        lambda: httpx.Client(base_url=cloudflare_dns._API_BASE, transport=httpx.MockTransport(recorder)),
    )

    with pytest.raises(cloudflare_dns.CloudflareApiError):
        cloudflare_dns.remove_public_hostname_route("demo-foo.nexatecpy.com")

    assert [c.method for c in recorder.calls] == ["GET"]


def test_remove_is_noop_when_nothing_exists(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(cloudflare_dns, "get_settings", lambda: settings)

    recorder = _Recorder({
        ("GET", "/client/v4/zones/zone-123/dns_records"): _json_response(_ok([])),
        ("GET", "/client/v4/accounts/acc-123/cfd_tunnel/tunnel-123/configurations"): _json_response(
            _ok({"config": {"ingress": [{"service": "http_status:404"}]}})
        ),
    })
    monkeypatch.setattr(
        cloudflare_dns, "_client",
        lambda: httpx.Client(base_url=cloudflare_dns._API_BASE, transport=httpx.MockTransport(recorder)),
    )

    cloudflare_dns.remove_public_hostname_route("demo-foo.nexatecpy.com")
    assert [c.method for c in recorder.calls] == ["GET", "GET"]


def test_tunnel_ingress_lock_blocks_other_connections():
    """El lock es de Postgres (no de memoria): mientras lo tiene uno, otra
    conexion -- como la del timer de barrido, que es otro proceso -- no
    puede tomarlo."""
    from sqlalchemy import text

    from app.core.db import engine

    with cloudflare_dns._tunnel_ingress_lock():
        with engine.connect() as other:
            got = other.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": cloudflare_dns._TUNNEL_INGRESS_LOCK_KEY}
            ).scalar()
            assert got is False

    with engine.connect() as other:
        got = other.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": cloudflare_dns._TUNNEL_INGRESS_LOCK_KEY}
        ).scalar()
        assert got is True
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": cloudflare_dns._TUNNEL_INGRESS_LOCK_KEY})
