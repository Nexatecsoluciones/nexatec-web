"""API para agentes (ChatGPT): la llave solo opera en SU sandbox DEMO, nunca
en otra empresa, en produccion, en el Control Center ni en el portal."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.api_keys import ApiKey
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantStatus
from app.security import api_keys
from app.services import agent_sandbox
from app.services.provisioning import force_drop_tenant_database_for_tests


@pytest.fixture(scope="module")
def sb():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-agent-{sfx}", name="ERP", short_description="x", category="ERP",
                    demo_available=True, production_available=True, is_active=True)
    db.add(system)
    db.commit()
    access_id, raw = agent_sandbox.create_sandbox(db, name=f"Sandbox Pytest {sfx}", system_slug=system.slug)
    access = db.get(SystemAccess, access_id)
    # Un acceso de PRODUCCION del mismo tenant y una llave atada a el (no deberia servir).
    prod = SystemAccess(tenant_id=access.tenant_id, system_id=system.id, environment=Environment.PRODUCTION,
                        status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    db.add(prod)
    db.commit()
    user_id = db.query(TenantUser.user_id).filter(TenantUser.tenant_id == access.tenant_id).scalar()
    _, prod_raw = api_keys.issue(db, name="prod", user_id=user_id, system_access_id=prod.id)
    db.commit()
    yield {"access": str(access.id), "prod": str(prod.id), "key": raw, "prod_key": prod_raw, "system": system.slug,
           "tenant_id": access.tenant_id, "user_id": user_id}
    for tdb in db.query(TenantDatabase).filter(TenantDatabase.tenant_id == access.tenant_id).all():
        force_drop_tenant_database_for_tests(tdb.database_identifier)
        db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
        db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
    db.query(ApiKey).filter(ApiKey.user_id == user_id).delete()
    db.query(UserSession).filter(UserSession.user_id == user_id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id == access.tenant_id).delete()
    db.query(SystemAccess).filter(SystemAccess.tenant_id == access.tenant_id).delete()
    db.query(AuditLog).filter(AuditLog.tenant_id == access.tenant_id).delete(synchronize_session=False)
    db.query(User).filter(User.id == user_id).delete()
    db.query(Tenant).filter(Tenant.id == access.tenant_id).delete()
    db.query(System).filter(System.slug == system.slug).delete()
    db.commit()
    db.close()


def _c(key=None):
    c = TestClient(app)
    if key:
        c.headers["Authorization"] = f"Bearer {key}"
    return c


def test_whoami_guide_and_auth(sb):
    c = _c(sb["key"])
    who = c.get("/api/agent/whoami").json()
    assert who["system_access_id"] == sb["access"] and who["environment"] == "DEMO" and who["role"] == "Administrador"
    assert "flujos_para_probar" in c.get("/api/agent/guide").json()
    assert _c().get("/api/agent/whoami").status_code == 401
    assert _c("nxk_" + "x" * 40).get("/api/agent/whoami").status_code == 401
    assert _c("otra-cosa").get("/api/agent/whoami").status_code == 401


def test_call_reads_and_writes_in_sandbox(sb):
    c = _c(sb["key"])
    r = c.post("/api/agent/call", json={"method": "GET", "path": "/products", "query": {"limit": 5}}).json()
    assert r["status"] == 200 and r["data"]["total"] > 0
    lead = c.post("/api/agent/call", json={"method": "POST", "path": "/crm/leads",
                                          "body": {"contact_name": "Prospecto GPT", "phone": "0981000000"}}).json()
    assert lead["status"] == 201 and lead["data"]["status"] == "OPEN"
    bad = c.post("/api/agent/call", json={"method": "POST", "path": "/crm/leads", "body": {"contact_name": "x"}}).json()
    assert bad["status"] == 422 and not bad["ok"]
    assert c.post("/api/agent/call", json={"method": "GET", "path": "/../admin/users"}).status_code == 422
    assert c.post("/api/agent/call", json={"method": "GET", "path": "/products?limit=1"}).status_code == 422


def test_key_cannot_leave_its_sandbox(sb):
    c = _c(sb["key"])
    # Otra empresa / otro acceso: como si no existiera.
    assert c.get(f"/api/erp/{uuid.uuid4()}/products").status_code == 404
    assert c.get(f"/api/erp/{sb['prod']}/products").status_code == 404
    # Una llave atada a un acceso de PRODUCCION no opera.
    assert _c(sb["prod_key"]).get(f"/api/erp/{sb['prod']}/products").status_code == 403
    # Fuera del ERP la llave no autentica (Control Center, portal, cuenta).
    for path in ("/api/admin/users", "/api/portal/my-systems", "/api/auth/me"):
        assert c.get(path).status_code == 401, path


def test_revoked_and_expired_keys(sb):
    db = SessionLocal()
    try:
        key, raw = api_keys.issue(db, name="tmp", user_id=sb["user_id"], system_access_id=uuid.UUID(sb["access"]))
        db.commit()
        assert _c(raw).get("/api/agent/whoami").status_code == 200
        assert agent_sandbox.revoke_key(db, key.prefix) >= 1
        assert _c(raw).get("/api/agent/whoami").status_code == 401
        k2, raw2 = api_keys.issue(db, name="vieja", user_id=sb["user_id"], system_access_id=uuid.UUID(sb["access"]))
        k2.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        assert _c(raw2).get("/api/agent/whoami").status_code == 401
        assert db.get(ApiKey, key.id).key_hash != raw  # solo hash en la base
    finally:
        db.close()


def test_catalog_and_openapi(sb):
    c = _c(sb["key"])
    cat = c.get("/api/agent/catalog", params={"module": "rrhh"}).json()
    assert cat["routes"] and all(r["module"] == "rrhh" for r in cat["routes"])
    po = next(r for r in c.get("/api/agent/catalog", params={"module": "compras"}).json()["routes"]
              if r["method"] == "POST" and r["path"] == "/purchases/orders")
    assert "supplier_id" in po["body"] and "lines" in po["body"]
    spec = _c().get("/api/agent/openapi.json").json()   # publica
    ops = [op for p in spec["paths"].values() for op in p.values()]
    assert {o["operationId"] for o in ops} == {"whoami", "guide", "catalog", "call"}
    mod = _c().get("/api/agent/openapi.json", params={"module": "crm"}).json()
    ids = [op["operationId"] for p in mod["paths"].values() for op in p.values()]
    assert len(ids) == len(set(ids)) and all(len(i) <= 64 for i in ids) and len(ids) <= 30
    assert spec["components"]["securitySchemes"]["apiKey"]["scheme"] == "bearer" and spec["security"] == [{"apiKey": []}]
    # Sin header/cookie de autenticacion como parametros sueltos y sin esquemas que no se usan.
    for s in (spec, mod):
        names = {x["name"] for p in s["paths"].values() for op in p.values() for x in op.get("parameters", [])}
        assert not names & {"authorization", "nexatec_session"}
    assert set(spec["components"]["schemas"]) <= {"CallIn", "HTTPValidationError", "ValidationError"}


def test_reset_only_for_sandboxes(sb):
    db = SessionLocal()
    try:
        with pytest.raises(agent_sandbox.SandboxError):
            agent_sandbox.reset_sandbox(db, uuid.UUID(sb["prod"]))
        c = _c(sb["key"])
        before = c.post("/api/agent/call", json={"method": "GET", "path": "/crm/leads"}).json()["data"]["total"]
        assert before >= 4  # 3 de la demo + el creado por el agente
        agent_sandbox.reset_sandbox(db, uuid.UUID(sb["access"]))
        after = c.post("/api/agent/call", json={"method": "GET", "path": "/crm/leads"}).json()
        assert after["status"] == 200 and after["data"]["total"] == 3
    finally:
        db.close()


def test_cli_create_is_safe_and_not_duplicated(sb, capsys):
    """Regresion: el CLI fallaba con DetachedInstanceError al imprimir el
    acceso (la llave nunca se mostraba). Ahora imprime acceso y llave, y una
    segunda corrida con el mismo nombre no crea otro sandbox."""
    import argparse

    from app import cli

    db = SessionLocal()
    name = f"Sandbox CLI {uuid.uuid4().hex[:6]}"
    try:
        assert cli.cmd_create_agent_sandbox(argparse.Namespace(name=name, system=sb["system"])) == 0
        out = capsys.readouterr().out
        assert "system_access_id:" in out and "nxk_" in out
        access_id = out.split("system_access_id:")[1].split()[0]
        assert cli.cmd_create_agent_sandbox(argparse.Namespace(name=name, system=sb["system"])) == 2
        err = capsys.readouterr().err
        assert access_id in err and "No se creo otro" in err and "nxk_" not in err
        assert db.query(Tenant).filter(Tenant.display_name == name).count() == 1
    finally:
        tenant = db.query(Tenant).filter(Tenant.display_name == name).one_or_none()
        if tenant is not None:
            for tdb in db.query(TenantDatabase).filter(TenantDatabase.tenant_id == tenant.id).all():
                force_drop_tenant_database_for_tests(tdb.database_identifier)
                db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
                db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
            uids = [u for (u,) in db.query(TenantUser.user_id).filter(TenantUser.tenant_id == tenant.id).all()]
            db.query(ApiKey).filter(ApiKey.user_id.in_(uids)).delete(synchronize_session=False)
            db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
            db.query(SystemAccess).filter(SystemAccess.tenant_id == tenant.id).delete()
            db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
            db.query(Tenant).filter(Tenant.id == tenant.id).delete()
            db.commit()
        db.close()


def test_rate_limit_expiry_and_audit(sb, monkeypatch):
    db = SessionLocal()
    try:
        key, raw = api_keys.issue(db, name="rate", user_id=sb["user_id"], system_access_id=uuid.UUID(sb["access"]))
        db.commit()
        days = (key.expires_at - datetime.now(timezone.utc)).days
        assert 89 <= days <= 90
        monkeypatch.setattr(api_keys, "RATE_PER_MINUTE", 3)
        c = _c(raw)
        assert [c.get("/api/agent/whoami").status_code for _ in range(4)] == [200, 200, 200, 429]
        monkeypatch.setattr(api_keys, "RATE_PER_MINUTE", 120)
        # Lo que hace el agente queda auditado a nombre de su usuario de servicio.
        c2 = _c(sb["key"])
        r = c2.post("/api/agent/call", json={"method": "POST", "path": "/crm/leads",
                                            "body": {"contact_name": "Auditado GPT", "phone": "0981000001"}}).json()
        assert r["status"] == 201
        log = db.query(AuditLog).filter(AuditLog.tenant_id == sb["tenant_id"], AuditLog.action == "ERP_CRM_LEAD_CREATED").all()
        assert any(entry.actor_user_id == sb["user_id"] for entry in log)
    finally:
        db.close()
