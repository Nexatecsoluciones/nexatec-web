"""Adjuntos de facturas de proveedor en Garage REAL: tipo por contenido,
limites, duplicados, permisos, aislamiento entre empresas y baja logica."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole as R, TenantMemberStatus, TenantStatus
from app.routers import erp_attachments
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services import storage
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database

PASSWORD = "ClaveDePruebaSegura123"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64


def _tenant(db, sfx, label):
    system = System(slug=f"pytest-att-{label}-{sfx}", name="ATT", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"att-{label}-{sfx}", legal_name="ATT", display_name="ATT", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.DEMO,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add(access)
    db.commit()
    tdb = provision_tenant_database(db, tenant.id, system.id, Environment.DEMO)
    return system, tenant, access, tdb


@pytest.fixture(scope="module")
def t():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    a = _tenant(db, sfx, "a")
    b = _tenant(db, sfx, "b")
    users, clients = [], {}
    for name, (sys_, tenant, access, _), role in (("fin", a, R.FINANCE), ("dep", a, R.WAREHOUSE), ("otro", b, R.CLIENT_ADMIN)):
        u = User(email=f"att-{name}-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER,
                 tenant_id=tenant.id)
        db.add(u)
        db.commit()
        db.add(TenantUser(tenant_id=tenant.id, user_id=u.id, role=role, status=TenantMemberStatus.ACTIVE))
        db.commit()
        users.append(u.id)
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": u.email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
        clients[name] = c
    base = f"/api/erp/{a[2].id}"
    inv = clients["fin"].get(f"{base}/supplier-invoices").json()["items"][0]
    yield {"c": clients, "base": base, "inv": inv["id"]}
    bucket = get_settings().storage_s3_bucket
    for sys_, tenant, access, tdb in (a, b):
        prefix = f"supplier-invoices/tenant-{tenant.id.hex[:8]}/"
        for o in storage._client().list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", []):
            storage.delete_object(o["Key"])
    for sys_, tenant, access, tdb in (a, b):
        force_drop_tenant_database_for_tests(tdb.database_identifier)
        db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
        db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
        db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
        db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
        db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(UserSession).filter(UserSession.user_id.in_(users)).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(users)).delete(synchronize_session=False)
    for sys_, tenant, access, tdb in (a, b):
        db.query(Tenant).filter(Tenant.id == tenant.id).delete()
        db.query(System).filter(System.id == sys_.id).delete()
    db.commit()
    db.close()


def _up(c, url, content, name="factura.pdf", ctype="application/pdf"):
    return c.post(url, files={"file": (name, content, ctype)})


def test_upload_list_download_and_remove(t):
    c, url = t["c"]["fin"], f"{t['base']}/supplier-invoices/{t['inv']}/attachments"
    r = _up(c, url, PDF, name="../../etc/Factura 001-002.pdf")
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["content_type"] == "application/pdf" and "/" not in att["filename"] and ".." not in att["filename"]
    assert _up(c, url, PDF).status_code == 409                       # mismo archivo
    assert _up(c, url, PNG, name="foto.jpg", ctype="image/jpeg").json()["content_type"] == "image/png"  # por contenido
    assert len(c.get(url).json()) == 2
    link = c.get(f"{url}/{att['id']}/url").json()
    assert "X-Amz-Signature" in link["url"] and link["expires_in"] == 300
    # El objeto existe en Garage, bajo la carpeta de facturas de proveedor.
    keys = [o["Key"] for o in storage._client().list_objects_v2(Bucket=get_settings().storage_s3_bucket,
                                                                 Prefix="supplier-invoices/").get("Contents", [])]
    assert any(k.endswith(".pdf") for k in keys)
    assert c.post(f"{url}/{att['id']}/remove", json={"reason": "Subida por error"}).status_code == 204
    assert c.get(f"{url}/{att['id']}/url").status_code == 404
    assert len(c.get(url).json()) == 1


def test_rejections(t, monkeypatch):
    c, url = t["c"]["fin"], f"{t['base']}/supplier-invoices/{t['inv']}/attachments"
    assert _up(c, url, b"esto no es un pdf, es texto", name="falso.pdf").status_code == 415
    assert _up(c, url, b"GIF89a" + b"\x00" * 20, name="x.gif", ctype="image/gif").status_code == 415
    assert _up(c, url, b"").status_code == 422
    monkeypatch.setattr(erp_attachments, "MAX_BYTES", 100)
    assert _up(c, url, PDF + b"0" * 200).status_code == 413
    assert _up(c, f"{t['base']}/supplier-invoices/{uuid.uuid4()}/attachments", PDF).status_code == 404


def test_permissions_and_isolation(t):
    url = f"{t['base']}/supplier-invoices/{t['inv']}/attachments"
    assert t["c"]["dep"].get(url).status_code == 403                 # deposito no ve cuentas por pagar
    assert _up(t["c"]["dep"], url, PDF + b"x").status_code == 403
    assert t["c"]["otro"].get(url).status_code == 404                # otra empresa: como si no existiera
    assert _up(t["c"]["otro"], url, PDF + b"y").status_code == 404
