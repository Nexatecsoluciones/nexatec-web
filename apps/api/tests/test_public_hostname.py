"""GET /api/public/hostname-context: lo que el frontend consulta ANTES de
cualquier login para saber a que tenant/sistema corresponde el subdominio
por el que entro el visitante. Cubre: resolucion valida via
X-Forwarded-Host (el camino real a traves del BFF de Next.js) y via Host
directo (dev/tests sin BFF), 404 generico para host desconocido/reservado,
y que X-Forwarded-Host tiene prioridad sobre Host cuando ambos vienen."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import Tenant
from app.models.system import System
from app.models.tenancy import TenantHostname
from app.models.tenancy_enums import Environment, TenantStatus
from app.services.hostname_resolution import register_tenant_hostname


@pytest.fixture()
def tenant_system_hostname():
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    system = System(
        slug=f"pytest-pubhost-system-{suffix}", name="Sistema Public Hostname", short_description="x",
        category="Test", demo_available=True, production_available=True, is_active=True,
    )
    tenant = Tenant(slug=f"pubhost-{suffix}", legal_name="Pub Host SRL", display_name="Pub Host", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    db.refresh(system)
    db.refresh(tenant)

    hostname = f"demo-{suffix}.nexatecpy.com"
    register_tenant_hostname(
        db, tenant_id=tenant.id, system_id=system.id, environment=Environment.DEMO, hostname=hostname,
    )
    db.commit()

    yield {"db": db, "tenant": tenant, "system": system, "hostname": hostname}

    db.query(TenantHostname).filter(TenantHostname.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def test_hostname_context_via_x_forwarded_host(tenant_system_hostname):
    ctx = tenant_system_hostname
    client = TestClient(app)
    resp = client.get("/api/public/hostname-context", headers={"X-Forwarded-Host": ctx["hostname"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_slug"] == ctx["tenant"].slug
    assert body["system_slug"] == ctx["system"].slug
    assert body["environment"] == "DEMO"


def test_hostname_context_via_plain_host(tenant_system_hostname):
    """Camino de dev/tests sin BFF: Host directo (lo que ve uvicorn)."""
    ctx = tenant_system_hostname
    client = TestClient(app)
    resp = client.get("/api/public/hostname-context", headers={"Host": ctx["hostname"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant_slug"] == ctx["tenant"].slug


def test_hostname_context_prefers_x_forwarded_host_over_host(tenant_system_hostname):
    """Si ambos headers vienen (el caso real: BFF + TestClient fuerza un
    Host de prueba), gana X-Forwarded-Host -- es el que de verdad viene
    del navegador, Host es solo el destino interno (loopback)."""
    ctx = tenant_system_hostname
    client = TestClient(app)
    resp = client.get(
        "/api/public/hostname-context",
        headers={"X-Forwarded-Host": ctx["hostname"], "Host": "127.0.0.1:4301"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant_slug"] == ctx["tenant"].slug


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Forwarded-Host": "nexatecpy.com"},
        {"X-Forwarded-Host": "no-existe-ninguna.nexatecpy.com"},
        {"X-Forwarded-Host": "'; DROP TABLE tenant_hostnames;--"},
        {"X-Forwarded-Host": "admin.nexatecpy.com"},
    ],
)
def test_hostname_context_returns_generic_404(headers):
    client = TestClient(app)
    resp = client.get("/api/public/hostname-context", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Hostname no reconocido."
