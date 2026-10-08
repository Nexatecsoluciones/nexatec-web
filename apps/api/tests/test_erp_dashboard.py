"""Tablero sobre una base DEMO real (con las operaciones de la empresa
ficticia): los KPIs tienen que coincidir con los documentos subyacentes."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database
from app.services.tenant_db_manager import tenant_db_manager

PASSWORD = "ClaveDePruebaSegura123"
D = Decimal


@pytest.fixture(scope="module")
def demo():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-dash-{sfx}", name="Dash", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"dash-{sfx}", legal_name="Dash", display_name="Dash", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    viewer = User(email=f"dash-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
    db.add(viewer)
    db.commit()
    db.add(TenantUser(tenant_id=tenant.id, user_id=viewer.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE))
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.DEMO,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add(access)
    db.commit()
    tdb = provision_tenant_database(db, tenant.id, system.id, Environment.DEMO)
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": viewer.email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
    yield {"c": c, "url": f"/api/erp/{access.id}/dashboard", "engine": tenant_db_manager.get_engine(tdb, tdb.credential)}
    force_drop_tenant_database_for_tests(tdb.database_identifier)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
    db.query(UserSession).filter(UserSession.user_id == viewer.id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
    db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
    db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(User).filter(User.id == viewer.id).delete()
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def test_dashboard_matches_underlying_documents(demo):
    r = demo["c"].get(demo["url"])
    assert r.status_code == 200, r.text
    k = r.json()
    with demo["engine"].connect() as conn:
        q = lambda sql: D(conn.execute(text(sql)).scalar())  # noqa: E731
        assert D(k["sales_total"]) == q("SELECT sum(total) FROM sales_invoices WHERE status='ISSUED'")
        assert k["invoice_count"] == 5
        assert D(k["receivables_open"]) == q("SELECT sum(balance_due) FROM sales_invoices WHERE status='ISSUED'")
        assert D(k["payables_open"]) == q("SELECT sum(balance_due) FROM supplier_invoices WHERE status='ISSUED'")
        assert D(k["stock_value"]) == q("SELECT round(sum(b.on_hand * p.average_cost)) FROM stock_balances b "
                                        "JOIN products p ON p.id = b.product_id")
    assert D(k["sales_net"]) < D(k["sales_total"])
    assert D(k["gross_margin"]) > 0 and D("0") < D(k["gross_margin_pct"]) < D("100")
    assert k["pending_orders"]["count"] == 1
    assert D(k["cash_and_bank"]) > 0
    assert len(k["top_products"]) == 5 and "gross_margin" in k["definitions"]
    assert sum(D(x["sales_total"]) for x in k["sales_by_day"]) == D(k["sales_total"])


def test_dashboard_range_validation(demo):
    assert demo["c"].get(demo["url"], params={"date_from": "2026-02-01", "date_to": "2026-01-01"}).status_code == 422
    assert demo["c"].get(demo["url"], params={"date_from": "2020-01-01", "date_to": "2026-01-01"}).status_code == 422
