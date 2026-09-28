"""FASE 4: multi-tenancy, entitlements, provisioning de demos y el test
critico de aislamiento (Tenant A nunca puede ver/tocar recursos de Tenant
B). Usa PostgreSQL real (nexatec_control + provisioning real de DBs de
demo) -- no mocks -- y limpia todo lo que crea, incluida la base fisica
provisionada."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import PasswordResetToken, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import (
    DemoInstance,
    SystemAccess,
    TenantDatabase,
    TenantDatabaseCredential,
    TenantUser,
)
from app.models.tenancy_enums import (
    Environment,
    ProvisioningStatus,
    SystemAccessStatus,
    TenantMemberRole,
    TenantMemberStatus,
    TenantStatus,
)
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.provisioning import force_drop_tenant_database_for_tests
from app.services.tenant_db_manager import tenant_db_manager

PASSWORD = "ClaveDePruebaSegura123"


def _make_user(db, email, role, tenant_id=None):
    db.query(UserSession).filter(
        UserSession.user_id.in_(db.query(User.id).filter(User.email == email))
    ).delete(synchronize_session=False)
    db.query(User).filter(User.email == email).delete()
    db.commit()
    user = User(email=email, password_hash=hash_password(PASSWORD), role=role, tenant_id=tenant_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _logged_in_client(email):
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"email": email, "password": PASSWORD, "turnstile_token": "dev"})
    assert resp.status_code == 200, resp.text
    return client


def _cleanup_tenant_database(tenant_db_id):
    db = SessionLocal()
    tenant_db = db.get(TenantDatabase, tenant_db_id)
    if tenant_db is not None:
        force_drop_tenant_database_for_tests(tenant_db.database_identifier)
    db.close()


@pytest.fixture()
def admin_client():
    db = SessionLocal()
    email = "pytest-t4-admin@example.com"
    user = _make_user(db, email, Role.SUPER_ADMIN)
    client = _logged_in_client(email)
    yield client
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def demo_system():
    db = SessionLocal()
    slug = "pytest-demo-system"
    db.query(System).filter(System.slug == slug).delete()
    db.commit()
    system = System(
        slug=slug, name="Sistema de Prueba T4", short_description="x",
        category="Test", demo_available=True, production_available=True, is_active=True,
    )
    db.add(system)
    db.commit()
    db.refresh(system)
    yield system
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def two_tenants_with_users():
    """Tenant A (con CLIENT_USER y CLIENT_ADMIN) y Tenant B (con su propio
    CLIENT_USER), para probar aislamiento cruzado."""
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]

    tenant_a = Tenant(slug=f"tenant-a-{suffix}", legal_name="Tenant A SRL", display_name="Tenant A", status=TenantStatus.ACTIVE)
    tenant_b = Tenant(slug=f"tenant-b-{suffix}", legal_name="Tenant B SRL", display_name="Tenant B", status=TenantStatus.ACTIVE)
    db.add_all([tenant_a, tenant_b])
    db.flush()

    user_a = _make_user(db, f"user-a-{suffix}@example.com", Role.CLIENT_USER, tenant_id=tenant_a.id)
    admin_a = _make_user(db, f"admin-a-{suffix}@example.com", Role.CLIENT_USER, tenant_id=tenant_a.id)
    user_b = _make_user(db, f"user-b-{suffix}@example.com", Role.CLIENT_USER, tenant_id=tenant_b.id)

    db.add(TenantUser(tenant_id=tenant_a.id, user_id=user_a.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE))
    db.add(TenantUser(tenant_id=tenant_a.id, user_id=admin_a.id, role=TenantMemberRole.CLIENT_ADMIN, status=TenantMemberStatus.ACTIVE))
    db.add(TenantUser(tenant_id=tenant_b.id, user_id=user_b.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE))
    db.commit()

    data = {
        "tenant_a": tenant_a, "tenant_b": tenant_b,
        "user_a": user_a, "admin_a": admin_a, "user_b": user_b,
        "client_a": _logged_in_client(user_a.email),
        "client_admin_a": _logged_in_client(admin_a.email),
        "client_b": _logged_in_client(user_b.email),
    }
    yield data

    for u in (user_a, admin_a, user_b):
        db.query(UserSession).filter(UserSession.user_id == u.id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id.in_([tenant_a.id, tenant_b.id])).delete(synchronize_session=False)
    db.query(SystemAccess).filter(SystemAccess.tenant_id.in_([tenant_a.id, tenant_b.id])).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_([user_a.id, admin_a.id, user_b.id])).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id.in_([tenant_a.id, tenant_b.id])).delete(synchronize_session=False)
    db.commit()
    db.close()


# --- Tenant CRUD ---------------------------------------------------------


def test_tenant_crud_authorized(admin_client):
    slug = f"acme-{uuid.uuid4().hex[:8]}"
    create = admin_client.post(
        "/api/admin/tenants",
        json={"slug": slug, "legal_name": "Acme SRL", "display_name": "Acme"},
    )
    assert create.status_code == 201, create.text
    tenant_id = create.json()["id"]

    get_resp = admin_client.get(f"/api/admin/tenants/{tenant_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["slug"] == slug

    update = admin_client.patch(f"/api/admin/tenants/{tenant_id}", json={"status": "SUSPENDED"})
    assert update.status_code == 200
    assert update.json()["status"] == "SUSPENDED"

    listed = admin_client.get("/api/admin/tenants")
    assert listed.status_code == 200
    assert any(t["id"] == tenant_id for t in listed.json())

    db = SessionLocal()
    db.query(Tenant).filter(Tenant.id == uuid.UUID(tenant_id)).delete()
    db.commit()
    db.close()


def test_tenant_crud_rejects_anonymous_and_client_role(two_tenants_with_users):
    anon = TestClient(app)
    assert anon.get("/api/admin/tenants").status_code == 401

    client_a = two_tenants_with_users["client_a"]
    assert client_a.get("/api/admin/tenants").status_code == 403
    assert client_a.post(
        "/api/admin/tenants", json={"slug": "xx", "legal_name": "xx", "display_name": "xx"}
    ).status_code == 403


def test_create_tenant_duplicate_slug_conflicts(admin_client):
    slug = f"dup-{uuid.uuid4().hex[:8]}"
    payload = {"slug": slug, "legal_name": "XX SRL", "display_name": "XX"}
    first = admin_client.post("/api/admin/tenants", json=payload)
    assert first.status_code == 201
    second = admin_client.post("/api/admin/tenants", json=payload)
    assert second.status_code == 409

    db = SessionLocal()
    db.query(Tenant).filter(Tenant.slug == slug).delete()
    db.commit()
    db.close()


# --- Membership / asignacion de usuarios ---------------------------------


def test_assign_new_user_creates_invite_token(admin_client):
    slug = f"invite-{uuid.uuid4().hex[:8]}"
    tenant = admin_client.post(
        "/api/admin/tenants", json={"slug": slug, "legal_name": "XX SRL", "display_name": "XX"}
    ).json()

    email = f"nuevo-{uuid.uuid4().hex[:8]}@example.com"
    resp = admin_client.post(
        f"/api/admin/tenants/{tenant['id']}/users",
        json={"email": email, "role": "CLIENT_ADMIN", "create_if_missing": True},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invite_token"] is not None
    assert body["role"] == "CLIENT_ADMIN"

    db = SessionLocal()
    user_row = db.query(User).filter(User.email == email).one()
    db.query(TenantUser).filter(TenantUser.tenant_id == uuid.UUID(tenant["id"])).delete()
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user_row.id).delete()
    db.query(User).filter(User.email == email).delete()
    db.query(Tenant).filter(Tenant.id == uuid.UUID(tenant["id"])).delete()
    db.commit()
    db.close()


def test_assign_missing_user_without_create_flag_404(admin_client):
    slug = f"noinv-{uuid.uuid4().hex[:8]}"
    tenant = admin_client.post(
        "/api/admin/tenants", json={"slug": slug, "legal_name": "XX SRL", "display_name": "XX"}
    ).json()

    resp = admin_client.post(
        f"/api/admin/tenants/{tenant['id']}/users",
        json={"email": "no-existe-nadie@example.com", "role": "CLIENT_USER"},
    )
    assert resp.status_code == 404

    db = SessionLocal()
    db.query(Tenant).filter(Tenant.id == uuid.UUID(tenant["id"])).delete()
    db.commit()
    db.close()


# --- Entitlements (system_access) ----------------------------------------


def test_create_entitlement_and_duplicate_conflicts(admin_client, two_tenants_with_users, demo_system):
    tenant_a = two_tenants_with_users["tenant_a"]
    payload = {"tenant_id": str(tenant_a.id), "system_id": str(demo_system.id), "environment": "DEMO"}

    first = admin_client.post("/api/admin/system-access", json=payload)
    assert first.status_code == 201, first.text

    second = admin_client.post("/api/admin/system-access", json=payload)
    assert second.status_code == 409

    db = SessionLocal()
    db.query(SystemAccess).filter(SystemAccess.tenant_id == tenant_a.id).delete()
    db.commit()
    db.close()


def test_invalid_environment_string_rejected(admin_client, two_tenants_with_users, demo_system):
    tenant_a = two_tenants_with_users["tenant_a"]
    resp = admin_client.post(
        "/api/admin/system-access",
        json={"tenant_id": str(tenant_a.id), "system_id": str(demo_system.id), "environment": "STAGING"},
    )
    assert resp.status_code == 422


def test_system_access_response_never_exposes_db_internals(admin_client, two_tenants_with_users, demo_system):
    tenant_a = two_tenants_with_users["tenant_a"]
    resp = admin_client.post(
        "/api/admin/system-access",
        json={"tenant_id": str(tenant_a.id), "system_id": str(demo_system.id), "environment": "PRODUCTION"},
    )
    assert resp.status_code == 201
    body = resp.json()
    for forbidden_field in ("database_name", "database_host_ref", "database_user_ref", "encrypted_password", "password"):
        assert forbidden_field not in body

    db = SessionLocal()
    db.query(SystemAccess).filter(SystemAccess.tenant_id == tenant_a.id).delete()
    db.commit()
    db.close()


# --- Portal filtering + IDOR ---------------------------------------------


def test_portal_shows_only_own_tenant_active_entitlements(admin_client, two_tenants_with_users, demo_system):
    tenant_a = two_tenants_with_users["tenant_a"]
    tenant_b = two_tenants_with_users["tenant_b"]
    db = SessionLocal()

    active_a = SystemAccess(tenant_id=tenant_a.id, system_id=demo_system.id, environment=Environment.DEMO, status=SystemAccessStatus.ACTIVE)
    pending_a = SystemAccess(tenant_id=tenant_a.id, system_id=demo_system.id, environment=Environment.PRODUCTION, status=SystemAccessStatus.PENDING)
    active_b = SystemAccess(tenant_id=tenant_b.id, system_id=demo_system.id, environment=Environment.DEMO, status=SystemAccessStatus.ACTIVE)
    db.add_all([active_a, pending_a, active_b])
    db.commit()
    db.refresh(active_a)
    db.refresh(active_b)

    resp_a = two_tenants_with_users["client_a"].get("/api/portal/my-systems")
    assert resp_a.status_code == 200
    ids_a = {row["system_access_id"] for row in resp_a.json()}
    assert str(active_a.id) in ids_a
    assert str(pending_a.id) not in ids_a  # PENDING nunca se muestra
    assert str(active_b.id) not in ids_a  # aislamiento: nunca datos de B

    resp_b = two_tenants_with_users["client_b"].get("/api/portal/my-systems")
    ids_b = {row["system_access_id"] for row in resp_b.json()}
    assert str(active_b.id) in ids_b
    assert str(active_a.id) not in ids_b

    db.query(SystemAccess).filter(SystemAccess.id.in_([active_a.id, pending_a.id, active_b.id])).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_idor_tenant_b_cannot_access_tenant_a_system_access(two_tenants_with_users, demo_system):
    tenant_a = two_tenants_with_users["tenant_a"]
    db = SessionLocal()
    access_a = SystemAccess(tenant_id=tenant_a.id, system_id=demo_system.id, environment=Environment.DEMO, status=SystemAccessStatus.ACTIVE)
    db.add(access_a)
    db.commit()
    db.refresh(access_a)

    # Tenant B intenta usar el "Acceder" de un system_access de Tenant A.
    resp = two_tenants_with_users["client_b"].post(f"/api/portal/my-systems/{access_a.id}/access")
    assert resp.status_code == 404  # nunca 200, nunca datos de A

    # Tenant A si puede.
    resp_ok = two_tenants_with_users["client_a"].post(f"/api/portal/my-systems/{access_a.id}/access")
    assert resp_ok.status_code == 200

    db.query(SystemAccess).filter(SystemAccess.id == access_a.id).delete()
    db.commit()
    db.close()


def test_idor_random_tampered_uuid_returns_404_not_500(two_tenants_with_users):
    random_id = uuid.uuid4()
    resp = two_tenants_with_users["client_a"].post(f"/api/portal/my-systems/{random_id}/access")
    assert resp.status_code == 404


def test_client_role_within_tenant_cannot_reach_admin_panel(two_tenants_with_users):
    # CLIENT_ADMIN (rol dentro del tenant) sigue sin ser un rol admin
    # GLOBAL: no debe entrar a /api/admin/*.
    resp = two_tenants_with_users["client_admin_a"].get("/api/admin/tenants")
    assert resp.status_code == 403


# --- Demo provisioning real (crea DB fisica) ------------------------------


def test_demo_full_lifecycle_provisions_real_isolated_database(admin_client, two_tenants_with_users, demo_system):
    tenant_a = two_tenants_with_users["tenant_a"]

    create = admin_client.post(
        "/api/admin/demos",
        json={"tenant_id": str(tenant_a.id), "system_id": str(demo_system.id), "duration_days": 7},
    )
    assert create.status_code == 201, create.text
    demo = create.json()
    assert demo["status"] == "READY"
    demo_id = demo["id"]

    db = SessionLocal()
    demo_row = db.get(DemoInstance, uuid.UUID(demo_id))
    access_row = db.get(SystemAccess, demo_row.system_access_id)
    tenant_db = db.get(TenantDatabase, demo_row.tenant_database_id)

    assert access_row.status == SystemAccessStatus.ACTIVE
    assert tenant_db.status == ProvisioningStatus.READY
    assert tenant_db.environment == Environment.DEMO

    # La base fisica existe de verdad y es alcanzable con sus propias
    # credenciales (no las de nexatec_app ni nexatec_provisioner).
    engine = tenant_db_manager.get_engine(tenant_db, tenant_db.credential)
    with engine.connect() as conn:
        current_db, current_user = conn.execute(text("SELECT current_database(), current_user")).one()
    assert current_db == tenant_db.database_name
    assert current_user == tenant_db.database_user_ref
    assert current_user != "nexatec_app"
    assert current_user != "nexatec_provisioner"

    # Doble-click: no debe poder crear una segunda demo para el mismo
    # tenant+sistema mientras la primera esta READY.
    duplicate = admin_client.post(
        "/api/admin/demos",
        json={"tenant_id": str(tenant_a.id), "system_id": str(demo_system.id), "duration_days": 7},
    )
    assert duplicate.status_code == 409

    # Renovar.
    renew = admin_client.post(f"/api/admin/demos/{demo_id}/renew", json={"extra_days": 5})
    assert renew.status_code == 200
    db.refresh(access_row)
    assert access_row.status == SystemAccessStatus.ACTIVE

    # Suspender.
    suspend = admin_client.post(f"/api/admin/demos/{demo_id}/suspend")
    assert suspend.status_code == 200
    db.refresh(access_row)
    assert access_row.status == SystemAccessStatus.SUSPENDED

    # Cleanup: DB fisica + filas de registro.
    tenant_db_manager.dispose_all()
    force_drop_tenant_database_for_tests(tenant_db.database_identifier)
    db.query(DemoInstance).filter(DemoInstance.id == demo_row.id).delete()
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tenant_db.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tenant_db.id).delete()
    db.query(SystemAccess).filter(SystemAccess.id == access_row.id).delete()
    db.commit()
    db.close()


def test_demo_creation_rejects_system_without_demo_available(admin_client, two_tenants_with_users):
    db = SessionLocal()
    slug = f"nodemo-{uuid.uuid4().hex[:8]}"
    system = System(slug=slug, name="Sin demo", short_description="x", category="x", demo_available=False, is_active=True)
    db.add(system)
    db.commit()
    db.refresh(system)

    resp = admin_client.post(
        "/api/admin/demos",
        json={"tenant_id": str(two_tenants_with_users["tenant_a"].id), "system_id": str(system.id)},
    )
    assert resp.status_code == 400

    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def test_sweep_expired_demos_marks_expired(admin_client, two_tenants_with_users, demo_system):
    tenant_a = two_tenants_with_users["tenant_a"]
    db = SessionLocal()
    expired_access = SystemAccess(
        tenant_id=tenant_a.id, system_id=demo_system.id, environment=Environment.DEMO,
        status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(expired_access)
    db.commit()
    db.refresh(expired_access)

    resp = admin_client.post("/api/admin/demos/sweep-expired")
    assert resp.status_code == 200
    assert resp.json()["expired_count"] >= 1

    db.refresh(expired_access)
    assert expired_access.status == SystemAccessStatus.EXPIRED

    db.query(SystemAccess).filter(SystemAccess.id == expired_access.id).delete()
    db.commit()
    db.close()
