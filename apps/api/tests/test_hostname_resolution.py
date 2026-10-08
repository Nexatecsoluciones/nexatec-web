"""Resolucion de tenant+sistema+entorno por Host header (ver
app/services/hostname_resolution.py). Cubre: resolucion valida, hosts
reservados, hosts invalidos/hostiles (host-header injection) y aislamiento
cruzado entre tenants -- ningun hostname de un tenant puede devolver otro
tenant."""

import uuid

import pytest

from app.core.db import SessionLocal
from app.models.control_plane import Tenant
from app.models.system import System
from app.models.tenancy import TenantHostname
from app.models.tenancy_enums import Environment, TenantStatus
from app.services.hostname_resolution import (
    normalize_host,
    register_tenant_hostname,
    resolve_hostname,
)


@pytest.fixture()
def tenant_and_system():
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    system = System(
        slug=f"pytest-host-system-{suffix}", name="Sistema Hostname Test", short_description="x",
        category="Test", demo_available=True, production_available=True, is_active=True,
    )
    tenant_a = Tenant(slug=f"host-a-{suffix}", legal_name="Tenant A SRL", display_name="Tenant A", status=TenantStatus.ACTIVE)
    tenant_b = Tenant(slug=f"host-b-{suffix}", legal_name="Tenant B SRL", display_name="Tenant B", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant_a, tenant_b])
    db.commit()
    db.refresh(system)
    db.refresh(tenant_a)
    db.refresh(tenant_b)
    yield {"db": db, "system": system, "tenant_a": tenant_a, "tenant_b": tenant_b, "suffix": suffix}
    db.query(TenantHostname).filter(TenantHostname.tenant_id.in_([tenant_a.id, tenant_b.id])).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id.in_([tenant_a.id, tenant_b.id])).delete(synchronize_session=False)
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


# --- normalize_host: saneo de hosts invalidos/hostiles -------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Demo-Foo.NexatecPY.com", "demo-foo.nexatecpy.com"),
        ("demo-foo.nexatecpy.com:4302", "demo-foo.nexatecpy.com"),
        ("  demo-foo.nexatecpy.com  ", "demo-foo.nexatecpy.com"),
    ],
)
def test_normalize_host_valid(raw, expected):
    assert normalize_host(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "a" * 300,
        "'; DROP TABLE tenants;--",
        "foo.com/../admin",
        "foo.com\x00bar",
        "foo..com",
        "-foo.com",
        "foo.com:abc",
        "foo.com:80:80",
        "[::1]:4302",
        "foo_bar.com",
        "demo-foo.nexatecpy.com/../../etc",
        "demo foo.nexatecpy.com",
    ],
)
def test_normalize_host_rejects_invalid(raw):
    assert normalize_host(raw) is None


# --- resolve_hostname: reservados y desconocidos --------------------------


@pytest.mark.parametrize(
    "reserved",
    ["nexatecpy.com", "www.nexatecpy.com", "admin.nexatecpy.com", "staging.nexatecpy.com", "API.NEXATECPY.COM"],
)
def test_resolve_hostname_reserved_never_resolves(reserved):
    db = SessionLocal()
    try:
        assert resolve_hostname(db, reserved) is None
    finally:
        db.close()


def test_resolve_hostname_unknown_returns_none():
    db = SessionLocal()
    try:
        assert resolve_hostname(db, f"no-existe-{uuid.uuid4().hex}.nexatecpy.com") is None
    finally:
        db.close()


def test_resolve_hostname_garbage_does_not_raise():
    db = SessionLocal()
    try:
        for payload in ("'; DROP TABLE tenant_hostnames;--", "\x00\x00\x00", "a" * 5000, "<script>alert(1)</script>.com"):
            assert resolve_hostname(db, payload) is None
    finally:
        db.close()


# --- register_tenant_hostname: validacion y unicidad ----------------------


def test_register_rejects_reserved(tenant_and_system):
    ctx = tenant_and_system
    with pytest.raises(ValueError):
        register_tenant_hostname(
            ctx["db"], tenant_id=ctx["tenant_a"].id, system_id=ctx["system"].id,
            environment=Environment.PRODUCTION, hostname="www.nexatecpy.com",
        )


def test_register_rejects_invalid_hostname(tenant_and_system):
    ctx = tenant_and_system
    with pytest.raises(ValueError):
        register_tenant_hostname(
            ctx["db"], tenant_id=ctx["tenant_a"].id, system_id=ctx["system"].id,
            environment=Environment.DEMO, hostname="'; DROP TABLE tenants;--",
        )


def test_register_rejects_duplicate(tenant_and_system):
    ctx = tenant_and_system
    hostname = f"demo-{ctx['suffix']}.nexatecpy.com"
    register_tenant_hostname(
        ctx["db"], tenant_id=ctx["tenant_a"].id, system_id=ctx["system"].id,
        environment=Environment.DEMO, hostname=hostname,
    )
    ctx["db"].commit()

    with pytest.raises(ValueError):
        register_tenant_hostname(
            ctx["db"], tenant_id=ctx["tenant_b"].id, system_id=ctx["system"].id,
            environment=Environment.DEMO, hostname=hostname,
        )


# --- resolucion correcta y aislamiento cruzado -----------------------------


def test_resolve_hostname_returns_correct_tenant(tenant_and_system):
    ctx = tenant_and_system
    hostname = f"demo-{ctx['suffix']}.nexatecpy.com"
    register_tenant_hostname(
        ctx["db"], tenant_id=ctx["tenant_a"].id, system_id=ctx["system"].id,
        environment=Environment.DEMO, hostname=hostname,
    )
    ctx["db"].commit()

    resolved = resolve_hostname(ctx["db"], hostname.upper() + ":4302")
    assert resolved is not None
    assert resolved.tenant_id == ctx["tenant_a"].id
    assert resolved.environment == Environment.DEMO


def test_resolve_hostname_isolation_between_tenants(tenant_and_system):
    """Dos tenants, cada uno con su propio hostname: el hostname de A
    jamas debe resolver a B ni viceversa, aunque comparten el mismo
    sistema."""
    ctx = tenant_and_system
    host_a = f"demo-a-{ctx['suffix']}.nexatecpy.com"
    host_b = f"demo-b-{ctx['suffix']}.nexatecpy.com"
    register_tenant_hostname(
        ctx["db"], tenant_id=ctx["tenant_a"].id, system_id=ctx["system"].id,
        environment=Environment.DEMO, hostname=host_a,
    )
    register_tenant_hostname(
        ctx["db"], tenant_id=ctx["tenant_b"].id, system_id=ctx["system"].id,
        environment=Environment.DEMO, hostname=host_b,
    )
    ctx["db"].commit()

    resolved_a = resolve_hostname(ctx["db"], host_a)
    resolved_b = resolve_hostname(ctx["db"], host_b)
    assert resolved_a.tenant_id == ctx["tenant_a"].id
    assert resolved_b.tenant_id == ctx["tenant_b"].id
    assert resolved_a.tenant_id != resolved_b.tenant_id
