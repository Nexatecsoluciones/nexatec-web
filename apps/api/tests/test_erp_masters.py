"""API de maestros del ERP sobre bases de tenant REALES (provisioning real,
sin mocks). Lo critico aca es el aislamiento: un usuario de B nunca ve ni
toca datos de A cambiando el system_access_id, y el personal de NEXATEC no
tiene acceso operacional implicito."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import (
    Environment,
    SystemAccessStatus,
    TenantMemberRole,
    TenantMemberStatus,
    TenantStatus,
)
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services import ruc as ruc_service
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database

PASSWORD = "ClaveDePruebaSegura123"


def _login(email: str) -> TestClient:
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"email": email, "password": PASSWORD, "turnstile_token": "dev"})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture(scope="module")
def erp():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-erp-{sfx}", name="ERP Test", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant_a = Tenant(slug=f"erp-a-{sfx}", legal_name="A SRL", display_name="A", status=TenantStatus.ACTIVE)
    tenant_b = Tenant(slug=f"erp-b-{sfx}", legal_name="B SRL", display_name="B", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant_a, tenant_b])
    db.commit()

    def user(email, role, tenant_id=None):
        u = User(email=email, password_hash=hash_password(PASSWORD), role=role, tenant_id=tenant_id)
        db.add(u)
        db.commit()
        return u

    admin_a = user(f"erp-admin-a-{sfx}@example.com", Role.CLIENT_USER, tenant_a.id)
    viewer_a = user(f"erp-viewer-a-{sfx}@example.com", Role.CLIENT_USER, tenant_a.id)
    admin_b = user(f"erp-admin-b-{sfx}@example.com", Role.CLIENT_USER, tenant_b.id)
    staff = user(f"erp-staff-{sfx}@example.com", Role.SUPER_ADMIN)
    db.add_all([
        TenantUser(tenant_id=tenant_a.id, user_id=admin_a.id, role=TenantMemberRole.CLIENT_ADMIN, status=TenantMemberStatus.ACTIVE),
        TenantUser(tenant_id=tenant_a.id, user_id=viewer_a.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE),
        TenantUser(tenant_id=tenant_b.id, user_id=admin_b.id, role=TenantMemberRole.CLIENT_ADMIN, status=TenantMemberStatus.ACTIVE),
    ])
    # PRODUCTION a proposito: arranca vacia (las DEMO vienen con la empresa
    # ficticia precargada, ver tests/test_demo_seed.py).
    access_a = SystemAccess(tenant_id=tenant_a.id, system_id=system.id, environment=Environment.PRODUCTION,
                            status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    access_b = SystemAccess(tenant_id=tenant_b.id, system_id=system.id, environment=Environment.PRODUCTION,
                            status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add_all([access_a, access_b])
    db.commit()
    db_a = provision_tenant_database(db, tenant_a.id, system.id, Environment.PRODUCTION)
    db_b = provision_tenant_database(db, tenant_b.id, system.id, Environment.PRODUCTION)

    ctx = {
        "a": f"/api/erp/{access_a.id}", "b": f"/api/erp/{access_b.id}",
        "access_a_id": access_a.id, "tenant_a_id": tenant_a.id,
        "admin_a": _login(admin_a.email), "viewer_a": _login(viewer_a.email),
        "admin_b": _login(admin_b.email), "staff": _login(staff.email),
    }
    yield ctx

    for d in (db_a, db_b):
        force_drop_tenant_database_for_tests(d.database_identifier)
        db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == d.id).delete()
        db.query(TenantDatabase).filter(TenantDatabase.id == d.id).delete()
    uids = [admin_a.id, viewer_a.id, admin_b.id, staff.id]
    db.query(UserSession).filter(UserSession.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(TenantUser).filter(TenantUser.tenant_id.in_([tenant_a.id, tenant_b.id])).delete(synchronize_session=False)
    db.query(SystemAccess).filter(SystemAccess.id.in_([access_a.id, access_b.id])).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.tenant_id.in_([tenant_a.id, tenant_b.id])).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id.in_([tenant_a.id, tenant_b.id])).delete(synchronize_session=False)
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


# --- RUC ----------------------------------------------------------------------


@pytest.mark.parametrize("base,dv", [("80028061", "0"), ("999160", "3"), ("2660", "3"), ("80000035", "8")])
def test_ruc_valid_vectors(base, dv):
    assert ruc_service.is_valid(base, dv)


@pytest.mark.parametrize("base,dv", [("80053249", "2"), ("80028061", "1"), ("abc", "1"), ("123456789", "0"), ("80028061", "")])
def test_ruc_invalid_vectors(base, dv):
    assert not ruc_service.is_valid(base, dv)


# --- CRUD y reglas ------------------------------------------------------------


def test_reference_data_visible(erp):
    c = erp["viewer_a"]
    assert {x["code"] for x in c.get(f"{erp['a']}/currencies").json()} == {"PYG", "USD"}
    assert {x["code"] for x in c.get(f"{erp['a']}/taxes").json()} == {"IVA10", "IVA5", "EXENTA"}
    assert any(u["code"] == "UN" for u in c.get(f"{erp['a']}/units").json())


def test_company_ruc_validation(erp):
    c = erp["admin_a"]
    bad = c.put(f"{erp['a']}/company", json={"legal_name": "Empresa A", "ruc": "80028061", "ruc_dv": "1"})
    assert bad.status_code == 422
    ok = c.put(f"{erp['a']}/company", json={"legal_name": "Empresa A", "ruc": "80028061", "ruc_dv": "0"})
    assert ok.status_code == 200, ok.text
    assert c.get(f"{erp['a']}/company").json()["legal_name"] == "Empresa A"


def test_full_masters_flow_and_rules(erp):
    c = erp["admin_a"]
    branch = c.post(f"{erp['a']}/branches", json={"code": "CEN", "name": "Casa central", "establishment_code": "001"})
    assert branch.status_code == 201, branch.text
    assert c.post(f"{erp['a']}/branches", json={"code": "CEN", "name": "Dup"}).status_code == 409

    wh = c.post(f"{erp['a']}/warehouses", json={"code": "DEP1", "name": "Deposito 1", "branch_id": branch.json()["id"]})
    assert wh.status_code == 201
    assert c.post(f"{erp['a']}/warehouses", json={"code": "X", "name": "X", "branch_id": str(uuid.uuid4())}).status_code == 404

    cat = c.post(f"{erp['a']}/product-categories", json={"name": "Bebidas"})
    assert cat.status_code == 201
    unit_id = next(u["id"] for u in c.get(f"{erp['a']}/units").json() if u["code"] == "UN")

    product = {"sku": "AGUA-500", "name": "Agua 500ml", "product_type": "GOOD", "category_id": cat.json()["id"],
               "unit_id": unit_id, "tax_code": "IVA10", "sale_price": "5000"}
    created = c.post(f"{erp['a']}/products", json=product)
    assert created.status_code == 201, created.text
    assert c.post(f"{erp['a']}/products", json=product).status_code == 409
    assert c.post(f"{erp['a']}/products", json={**product, "sku": "N1", "sale_price": "-1"}).status_code == 422
    assert c.post(f"{erp['a']}/products", json={**product, "sku": "N2", "tax_code": "IVA99"}).status_code == 422
    service = {**product, "sku": "SRV-1", "name": "Instalacion", "product_type": "SERVICE", "tracks_stock": True}
    assert c.post(f"{erp['a']}/products", json=service).status_code == 422
    implicit = {k: v for k, v in service.items() if k != "tracks_stock"}
    srv = c.post(f"{erp['a']}/products", json=implicit)
    assert srv.status_code == 201 and srv.json()["tracks_stock"] is False

    patched = c.patch(f"{erp['a']}/products/{created.json()['id']}", json={"sale_price": "5500"})
    assert patched.status_code == 200 and patched.json()["sale_price"] in ("5500", "5500.00")

    page = c.get(f"{erp['a']}/products", params={"q": "agua", "limit": 10}).json()
    assert page["total"] == 1 and page["items"][0]["sku"] == "AGUA-500"
    assert c.get(f"{erp['a']}/products", params={"limit": 1}).json()["total"] == 2
    assert len(c.get(f"{erp['a']}/products", params={"limit": 1}).json()["items"]) == 1

    party = c.post(f"{erp['a']}/parties", json={"legal_name": "Cliente Uno SA", "ruc": "80000035", "ruc_dv": "8",
                                                 "is_customer": True, "credit_limit": "1000000", "payment_terms_days": 30})
    assert party.status_code == 201, party.text
    assert party.json()["ruc_status"] == "FORMAT_OK"
    assert c.post(f"{erp['a']}/parties", json={"legal_name": "Otro", "ruc": "80000035", "ruc_dv": "8",
                                                "is_customer": True}).status_code == 409
    assert c.post(f"{erp['a']}/parties", json={"legal_name": "Sin rol"}).status_code == 422
    assert c.patch(f"{erp['a']}/parties/{party.json()['id']}", json={"is_customer": False}).status_code == 422
    assert c.get(f"{erp['a']}/parties", params={"role": "supplier"}).json()["total"] == 0


def test_writes_are_audited_in_control_plane(erp):
    db = SessionLocal()
    actions = {a for (a,) in db.query(AuditLog.action).filter(AuditLog.tenant_id == erp["tenant_a_id"]).all()}
    db.close()
    assert {"ERP_PRODUCT_CREATED", "ERP_PARTY_CREATED", "ERP_BRANCH_CREATED"} <= actions


# --- Permisos y aislamiento ---------------------------------------------------


def test_viewer_can_read_but_not_write(erp):
    c = erp["viewer_a"]
    assert c.get(f"{erp['a']}/products").status_code == 200
    assert c.post(f"{erp['a']}/branches", json={"code": "V1", "name": "x"}).status_code == 403
    assert c.put(f"{erp['a']}/company", json={"legal_name": "Hack"}).status_code == 403


def test_other_tenant_cannot_reach_tenant_a(erp):
    b = erp["admin_b"]
    assert b.get(f"{erp['a']}/products").status_code == 404
    assert b.post(f"{erp['a']}/branches", json={"code": "B1", "name": "x"}).status_code == 404
    # Y su propia base no ve nada de A.
    assert b.get(f"{erp['b']}/products").json()["total"] == 0


def test_nexatec_staff_has_no_implicit_operational_access(erp):
    assert erp["staff"].get(f"{erp['a']}/products").status_code == 404


def test_anonymous_and_unknown_access(erp):
    assert TestClient(app).get(f"{erp['a']}/products").status_code == 401
    assert erp["admin_a"].get(f"/api/erp/{uuid.uuid4()}/products").status_code == 404


def test_expired_access_and_suspended_tenant_are_blocked(erp):
    db = SessionLocal()
    access = db.get(SystemAccess, erp["access_a_id"])
    original_expiry = access.expires_at
    access.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert erp["admin_a"].get(f"{erp['a']}/products").status_code == 403
    access.expires_at = original_expiry
    db.commit()

    tenant = db.get(Tenant, erp["tenant_a_id"])
    tenant.status = TenantStatus.SUSPENDED
    db.commit()
    assert erp["admin_a"].get(f"{erp['a']}/products").status_code == 403
    tenant.status = TenantStatus.ACTIVE
    db.commit()
    assert erp["admin_a"].get(f"{erp['a']}/products").status_code == 200
    db.close()
