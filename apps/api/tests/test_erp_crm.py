"""CRM sobre una base de tenant REAL: prospectos, conversion a cliente,
oportunidades con etapas, cierre inmutable, actividades y embudo."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database

PASSWORD = "ClaveDePruebaSegura123"
D = Decimal


@pytest.fixture(scope="module")
def p():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-crm-{sfx}", name="CRM", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"crm-{sfx}", legal_name="CRM SRL", display_name="CRM", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    admin = User(email=f"crm-admin-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER,
                 tenant_id=tenant.id)
    db.add(admin)
    db.commit()
    db.add(TenantUser(tenant_id=tenant.id, user_id=admin.id, role=TenantMemberRole.CLIENT_ADMIN, status=TenantMemberStatus.ACTIVE))
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.PRODUCTION,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add(access)
    db.commit()
    tdb = provision_tenant_database(db, tenant.id, system.id, Environment.PRODUCTION)
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": admin.email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
    base = f"/api/erp/{access.id}/crm"

    def lead(**kw):
        body = {"contact_name": "Maria Gomez", "company_name": f"Ferreteria {uuid.uuid4().hex[:5]}", "phone": "0981123456",
                "source": "WhatsApp"} | kw
        r = c.post(f"{base}/leads", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    yield {"c": c, "base": base, "erp": f"/api/erp/{access.id}", "lead": lead, "tenant_id": tenant.id}

    force_drop_tenant_database_for_tests(tdb.database_identifier)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
    db.query(UserSession).filter(UserSession.user_id == admin.id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
    db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
    db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(User).filter(User.id == admin.id).delete()
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def test_lead_requires_contact(p):
    r = p["c"].post(f"{p['base']}/leads", json={"contact_name": "Sin datos"})
    assert r.status_code == 422


def test_full_cycle_lead_to_won(p):
    c, base = p["c"], p["base"]
    lead = p["lead"]()
    opp = c.post(f"{base}/opportunities", json={"title": "Sistema de stock", "lead_id": lead["id"], "amount": "5000000"}).json()
    assert opp["number"].startswith("OP-") and opp["stage"] == "NEW" and opp["probability"] == 10
    assert opp["account_name"] == lead["company_name"]
    act = c.post(f"{base}/activities", json={"kind": "CALL", "subject": "Llamar para demo", "opportunity_id": opp["id"],
                                            "due_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}).json()
    assert act["lead_id"] == lead["id"] and act["done_at"] is None

    r = c.post(f"{base}/opportunities/{opp['id']}/stage", json={"stage": "PROPOSAL"})
    assert r.status_code == 200 and r.json()["probability"] == 50
    # No se gana mientras sea de un prospecto.
    assert c.post(f"{base}/opportunities/{opp['id']}/win").status_code == 409

    conv = c.post(f"{base}/leads/{lead['id']}/convert")
    assert conv.status_code == 200 and conv.json()["status"] == "CONVERTED"
    party_id = conv.json()["party_id"]
    party = c.get(f"{p['erp']}/parties/{party_id}").json()
    assert party["is_customer"] and party["legal_name"] == lead["company_name"] and party["ruc"] is None
    moved = c.get(f"{base}/opportunities/{opp['id']}").json()
    assert moved["party_id"] == party_id and moved["lead_id"] is None
    assert c.get(f"{base}/activities", params={"party_id": party_id}).json()["total"] == 1
    # Un prospecto convertido no se vuelve a convertir.
    assert c.post(f"{base}/leads/{lead['id']}/convert").status_code == 409

    won = c.post(f"{base}/opportunities/{opp['id']}/win").json()
    assert won["stage"] == "WON" and won["probability"] == 100 and won["closed_at"]
    # Cerrada = inmutable.
    assert c.patch(f"{base}/opportunities/{opp['id']}", json={"amount": "1"}).status_code == 409
    assert c.post(f"{base}/opportunities/{opp['id']}/stage", json={"stage": "NEW"}).status_code == 409
    assert c.post(f"{base}/opportunities/{opp['id']}/lose", json={"reason": "cambio"}).status_code == 409

    done = c.post(f"{base}/activities/{act['id']}/complete", json={"notes": "Acepto la demo"})
    assert done.status_code == 200 and done.json()["done_at"]
    assert c.post(f"{base}/activities/{act['id']}/complete", json={}).status_code == 409


def test_lose_requires_reason_and_close_stages_not_via_move(p):
    c, base = p["c"], p["base"]
    lead = p["lead"]()
    opp = c.post(f"{base}/opportunities", json={"title": "Perdible", "lead_id": lead["id"]}).json()
    assert c.post(f"{base}/opportunities/{opp['id']}/stage", json={"stage": "WON"}).status_code == 422
    assert c.post(f"{base}/opportunities/{opp['id']}/lose", json={"reason": ""}).status_code == 422
    lost = c.post(f"{base}/opportunities/{opp['id']}/lose", json={"reason": "Precio"}).json()
    assert lost["stage"] == "LOST" and lost["lost_reason"] == "Precio"


def test_opportunity_needs_exactly_one_account(p):
    c, base = p["c"], p["base"]
    lead = p["lead"]()
    sup = c.post(f"{p['erp']}/parties", json={"legal_name": "Solo Proveedor", "is_supplier": True}).json()
    assert c.post(f"{base}/opportunities", json={"title": "Sin cuenta"}).status_code == 422
    assert c.post(f"{base}/opportunities", json={"title": "Proveedor", "party_id": sup["id"]}).status_code == 422
    cust = c.post(f"{p['erp']}/parties", json={"legal_name": "Cliente Uno", "is_customer": True}).json()
    assert c.post(f"{base}/opportunities", json={"title": "Doble", "party_id": cust["id"], "lead_id": lead["id"]}).status_code == 422


def test_discard_blocked_with_open_opportunities(p):
    c, base = p["c"], p["base"]
    lead = p["lead"]()
    opp = c.post(f"{base}/opportunities", json={"title": "Abierta", "lead_id": lead["id"]}).json()
    assert c.post(f"{base}/leads/{lead['id']}/discard", json={"reason": "No responde"}).status_code == 409
    c.post(f"{base}/opportunities/{opp['id']}/lose", json={"reason": "No responde"})
    r = c.post(f"{base}/leads/{lead['id']}/discard", json={"reason": "No responde"})
    assert r.status_code == 200 and r.json()["status"] == "DISCARDED"
    assert c.patch(f"{base}/leads/{lead['id']}", json={"phone": "1"}).status_code == 409


def test_pipeline_forecast(p):
    c, base = p["c"], p["base"]
    before = c.get(f"{base}/pipeline").json()
    cust = c.post(f"{p['erp']}/parties", json={"legal_name": f"Cliente {uuid.uuid4().hex[:5]}", "is_customer": True}).json()
    a = c.post(f"{base}/opportunities", json={"title": "Opo A", "party_id": cust["id"], "amount": "1000000"}).json()
    assert "id" in a, a
    c.post(f"{base}/opportunities/{a['id']}/stage", json={"stage": "NEGOTIATION"})
    b = c.post(f"{base}/opportunities", json={"title": "Opo B", "party_id": cust["id"], "amount": "400000"}).json()
    c.post(f"{base}/opportunities/{b['id']}/stage", json={"stage": "QUALIFIED", "probability": 40})
    after = c.get(f"{base}/pipeline").json()
    assert D(after["open_amount"]) - D(before["open_amount"]) == D("1400000")
    # 75% de 1.000.000 + 40% de 400.000
    assert D(after["forecast"]) - D(before["forecast"]) == D("910000")
    assert after["overdue_activities"] >= 0 and after["win_rate"] is not None
    listed = c.get(f"{base}/opportunities", params={"open_only": True, "party_id": cust["id"]}).json()
    assert listed["total"] == 2 and all(i["account_name"] == cust["legal_name"] for i in listed["items"])


def test_activity_note_is_done_and_needs_subject_ref(p):
    c, base = p["c"], p["base"]
    lead = p["lead"]()
    assert c.post(f"{base}/activities", json={"kind": "NOTE", "subject": "Suelta"}).status_code == 422
    note = c.post(f"{base}/activities", json={"kind": "NOTE", "subject": "Prefiere WhatsApp", "lead_id": lead["id"]}).json()
    assert note["done_at"] is not None
    pend = c.get(f"{base}/activities", params={"pending": True, "lead_id": lead["id"]}).json()
    assert pend["total"] == 0
