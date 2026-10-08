"""Cierre del MVP: bootstrap publico, Product Studio (schema no-code),
Configuration Center (versionado + secrets nunca expuestos), anti-SSRF
del Service Registry, y aprobacion de DemoRequest de punta a punta."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.control_center import DemoRequest, InstanceConfiguration, InstanceSecretValue, ProvisioningJob, ServiceRegistry
from app.models.control_plane import PasswordResetToken, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import DemoInstance, SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantHostname, TenantUser
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.provisioning import force_drop_tenant_database_for_tests

PASSWORD = "ClaveDePruebaSegura123"


def _login(email):
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"email": email, "password": PASSWORD, "turnstile_token": "dev"})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture()
def admin_client():
    db = SessionLocal()
    email = "pytest-cc-admin@example.com"
    db.query(UserSession).filter(UserSession.user_id.in_(db.query(User.id).filter(User.email == email))).delete(synchronize_session=False)
    db.query(User).filter(User.email == email).delete()
    db.commit()
    user = User(email=email, full_name="CC Admin", password_hash=hash_password(PASSWORD), role=Role.SUPER_ADMIN)
    db.add(user)
    db.commit()
    client = _login(email)
    yield client
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def product():
    db = SessionLocal()
    slug = f"prod-test-{uuid.uuid4().hex[:8]}"
    system = System(
        slug=slug, name="Producto de prueba", short_description="x", category="Test",
        demo_available=True, is_active=True, is_public=True,
        config_schema=[
            {"key": "moneda", "label": "Moneda", "type": "select", "required": True, "options": ["PYG", "USD"]},
            {"key": "api_key", "label": "API Key externa", "type": "secret", "required": False},
        ],
        modules_schema=[{"key": "reportes", "label": "Reportes", "default_enabled": False}],
    )
    db.add(system)
    db.commit()
    db.refresh(system)
    yield system
    db.query(InstanceConfiguration).filter(InstanceConfiguration.system_id == system.id).delete()
    db.query(InstanceSecretValue).filter(InstanceSecretValue.system_id == system.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def tenant():
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    t = Tenant(slug=f"cc-t-{suffix}", legal_name="CC Test", display_name="CC Test", status="ACTIVE")
    db.add(t)
    db.commit()
    db.refresh(t)
    yield t
    db.query(InstanceConfiguration).filter(InstanceConfiguration.tenant_id == t.id).delete()
    db.query(InstanceSecretValue).filter(InstanceSecretValue.tenant_id == t.id).delete()
    db.query(Tenant).filter(Tenant.id == t.id).delete()
    db.commit()
    db.close()


# --- Bootstrap ---

def test_bootstrap_status_reflects_real_state(admin_client):
    resp = TestClient(app).get("/api/system/bootstrap-status")
    assert resp.status_code == 200
    assert resp.json() == {"initialized": True}


# --- Product Studio / schema no-code ---

def test_product_template_rejects_invalid_field_type(admin_client):
    resp = admin_client.post("/api/admin/systems", json={
        "slug": f"bad-{uuid.uuid4().hex[:6]}", "name": "x", "short_description": "x", "category": "x",
        "config_schema": [{"key": "campo", "label": "Campo", "type": "javascript"}],
    })
    assert resp.status_code == 422


def test_product_template_rejects_duplicate_keys(admin_client):
    resp = admin_client.post("/api/admin/systems", json={
        "slug": f"dup-{uuid.uuid4().hex[:6]}", "name": "x", "short_description": "x", "category": "x",
        "config_schema": [
            {"key": "moneda", "label": "Moneda", "type": "text"},
            {"key": "moneda", "label": "Moneda 2", "type": "text"},
        ],
    })
    assert resp.status_code == 422


def test_product_template_client_role_cannot_create(tenant):
    db = SessionLocal()
    user = User(email=f"cc-client-{uuid.uuid4().hex[:6]}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
    db.add(user)
    db.commit()
    client = _login(user.email)

    resp = client.post("/api/admin/systems", json={
        "slug": "x", "name": "x", "short_description": "x", "category": "x",
    })
    assert resp.status_code == 403

    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    db.close()


# --- Configuration Center ---

def test_config_versioning_and_history(admin_client, tenant, product):
    r1 = admin_client.post(
        f"/api/admin/config/{tenant.id}/{product.id}/DEMO",
        json={"config": {"moneda": "PYG"}, "modules": {"reportes": True}, "branding": {"primary_color": "#0b4b42"}},
    )
    assert r1.status_code == 201, r1.text
    assert r1.json()["version"] == 1

    r2 = admin_client.post(
        f"/api/admin/config/{tenant.id}/{product.id}/DEMO",
        json={"config": {"moneda": "USD"}, "modules": {"reportes": False}, "branding": {}},
    )
    assert r2.status_code == 201
    assert r2.json()["version"] == 2

    current = admin_client.get(f"/api/admin/config/{tenant.id}/{product.id}/DEMO")
    assert current.json()["version"] == 2
    assert current.json()["config"]["moneda"] == "USD"

    history = admin_client.get(f"/api/admin/config/{tenant.id}/{product.id}/DEMO/history")
    versions = [v["version"] for v in history.json()]
    assert versions == [2, 1]


def test_config_rejects_value_not_in_schema_options(admin_client, tenant, product):
    resp = admin_client.post(
        f"/api/admin/config/{tenant.id}/{product.id}/DEMO",
        json={"config": {"moneda": "EUR"}, "modules": {}, "branding": {}},
    )
    assert resp.status_code == 400


def test_config_rejects_secret_field_in_normal_config(admin_client, tenant, product):
    resp = admin_client.post(
        f"/api/admin/config/{tenant.id}/{product.id}/DEMO",
        json={"config": {"moneda": "PYG", "api_key": "deberia-ir-por-otro-lado"}, "modules": {}, "branding": {}},
    )
    assert resp.status_code == 400


def test_secret_value_never_returned_only_configured_status(admin_client, tenant, product):
    set_resp = admin_client.put(
        f"/api/admin/config/{tenant.id}/{product.id}/DEMO/secrets/api_key",
        json={"value": "sk-super-secreta-de-verdad"},
    )
    assert set_resp.status_code == 204

    status_resp = admin_client.get(f"/api/admin/config/{tenant.id}/{product.id}/DEMO/secrets")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body == [{"key": "api_key", "configured": True, "updated_at": body[0]["updated_at"]}]
    assert "sk-super-secreta-de-verdad" not in status_resp.text

    db = SessionLocal()
    row = db.query(InstanceSecretValue).filter(InstanceSecretValue.tenant_id == tenant.id).one()
    assert row.encrypted_value != "sk-super-secreta-de-verdad"
    db.close()


# --- Anti-SSRF service registry ---

def test_service_registry_rejects_arbitrary_internal_host(admin_client):
    resp = admin_client.post("/api/admin/service-registry", json={
        "name": "Malicioso", "type": "INTERNAL_SERVICE", "internal_target": "10.0.0.5:9999",
    })
    assert resp.status_code == 400


def test_service_registry_rejects_disallowed_port(admin_client):
    resp = admin_client.post("/api/admin/service-registry", json={
        "name": "Puerto no permitido", "type": "INTERNAL_SERVICE", "internal_target": "127.0.0.1:22",
    })
    assert resp.status_code == 400


def test_service_registry_accepts_known_internal_port(admin_client):
    resp = admin_client.post("/api/admin/service-registry", json={
        "name": "API interna", "type": "INTERNAL_SERVICE", "internal_target": "127.0.0.1:4301",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert "internal_target" not in body  # nunca se devuelve al cliente

    db = SessionLocal()
    db.query(ServiceRegistry).filter(ServiceRegistry.id == uuid.UUID(body["id"])).delete()
    db.commit()
    db.close()


def test_service_registry_only_super_admin(tenant):
    db = SessionLocal()
    staff = User(email=f"cc-support-{uuid.uuid4().hex[:6]}@example.com", password_hash=hash_password(PASSWORD), role=Role.SUPPORT)
    db.add(staff)
    db.commit()
    client = _login(staff.email)

    resp = client.get("/api/admin/service-registry")
    assert resp.status_code == 403

    db.query(UserSession).filter(UserSession.user_id == staff.id).delete()
    db.query(User).filter(User.id == staff.id).delete()
    db.commit()
    db.close()


# --- Demo request end-to-end ---

def test_demo_request_approval_creates_tenant_and_provisions(admin_client, product):
    create = TestClient(app).post("/api/demo-requests", json={
        "contact_name": "Persona Interesada", "contact_email": f"interesado-{uuid.uuid4().hex[:6]}@example.com",
        "company_name": "Empresa Interesada", "system_id": str(product.id), "turnstile_token": "dev",
        "accept_privacy": True, "privacy_version": "test",
    })
    assert create.status_code == 201
    request_id = create.json()["id"]

    approve = admin_client.post(f"/api/admin/demo-requests/{request_id}/approve", json={"duration_days": 7})
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["job_status"] == "SUCCESS"
    assert body["invite_token"] is not None
    assert body["demo_request"]["status"] == "PROVISIONED"

    tenant_id = uuid.UUID(body["tenant_id"])
    db = SessionLocal()
    access = db.query(SystemAccess).filter(SystemAccess.tenant_id == tenant_id).one()
    assert access.status.value == "ACTIVE"
    demo = db.query(DemoInstance).filter(DemoInstance.system_access_id == access.id).one()
    tenant_db = db.get(TenantDatabase, demo.tenant_database_id)
    assert tenant_db.status.value == "READY"

    # cleanup completo, incluida la base fisica real
    force_drop_tenant_database_for_tests(tenant_db.database_identifier)
    assert any(st["name"] == "Subdominio" for st in db.query(ProvisioningJob).filter(
        ProvisioningJob.tenant_id == tenant_id).one().steps)
    db.query(TenantHostname).filter(TenantHostname.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(ProvisioningJob).filter(ProvisioningJob.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tenant_db.id).delete()
    db.query(DemoInstance).filter(DemoInstance.id == demo.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tenant_db.id).delete()
    db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
    db.query(DemoRequest).filter(DemoRequest.id == uuid.UUID(request_id)).delete()
    users = db.query(User).filter(User.tenant_id == tenant_id).all()
    uids = [u.id for u in users]
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tenant_id).delete()
    db.commit()
    db.close()


def test_demo_request_anonymous_cannot_skip_approval(product):
    # Un visitante no puede llamar directo al endpoint de aprobacion.
    create = TestClient(app).post("/api/demo-requests", json={
        "contact_name": "Xavier", "contact_email": f"x-{uuid.uuid4().hex[:6]}@example.com",
        "system_id": str(product.id), "turnstile_token": "dev",
        "accept_privacy": True, "privacy_version": "test",
    })
    request_id = create.json()["id"]

    resp = TestClient(app).post(f"/api/admin/demo-requests/{request_id}/approve", json={})
    assert resp.status_code == 401

    db = SessionLocal()
    db.query(DemoRequest).filter(DemoRequest.id == uuid.UUID(request_id)).delete()
    db.commit()
    db.close()


# --- Mass assignment ---

def test_user_create_rejects_mismatched_role_tenant_combo(admin_client):
    resp = admin_client.post("/api/admin/users", json={
        "email": f"masstest-{uuid.uuid4().hex[:6]}@example.com", "full_name": "Alguien",
        "role": "SUPER_ADMIN", "tenant_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 400


def _demo_body(product, email, **extra):
    return {"contact_name": "Interesado", "contact_email": email, "system_id": str(product.id), "turnstile_token": "dev",
            "accept_privacy": True, "privacy_version": "2026-10-08", **extra}


def test_demo_request_requires_privacy_and_valid_input(product):
    c = TestClient(app)
    email = f"priv-{uuid.uuid4().hex[:6]}@example.com"
    assert c.post("/api/demo-requests", json=_demo_body(product, email, accept_privacy=False)).status_code == 422
    assert c.post("/api/demo-requests", json=_demo_body(product, email, contact_name="x" * 500)).status_code == 422
    assert c.post("/api/demo-requests", json=_demo_body(product, email, system_id=str(uuid.uuid4()))).status_code == 422
    ok = c.post("/api/demo-requests", json=_demo_body(product, email.upper()))
    assert ok.status_code == 201 and ok.json()["contact_email"] == email
    db = SessionLocal()
    row = db.get(DemoRequest, uuid.UUID(ok.json()["id"]))
    assert row.privacy_version == "2026-10-08" and row.privacy_accepted_at is not None
    db.delete(row)
    db.commit()
    db.close()


def test_demo_request_rate_limited_per_email(product):
    c = TestClient(app)
    email = f"rate-{uuid.uuid4().hex[:6]}@example.com"
    codes = [c.post("/api/demo-requests", json=_demo_body(product, email)).status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]
    db = SessionLocal()
    db.query(DemoRequest).filter(DemoRequest.contact_email == email).delete()
    db.commit()
    db.close()


def test_demo_request_rate_limited_per_ip(product, monkeypatch):
    """Con una IP real (no la del cliente de pruebas, que safe_ip descarta):
    esta rama no se ejercitaba y fallaba en staging (INET = varchar)."""
    from app.routers import demo_requests as dr

    monkeypatch.setattr(dr, "safe_ip", lambda _value: "203.0.113.7")
    c = TestClient(app)
    emails = [f"ip-{i}-{uuid.uuid4().hex[:6]}@example.com" for i in range(6)]
    codes = [c.post("/api/demo-requests", json=_demo_body(product, e)).status_code for e in emails]
    assert codes == [201] * 5 + [429]
    db = SessionLocal()
    db.query(DemoRequest).filter(DemoRequest.contact_email.in_(emails)).delete(synchronize_session=False)
    db.commit()
    db.close()
