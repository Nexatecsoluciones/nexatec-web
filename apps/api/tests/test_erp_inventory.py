"""Inventario sobre una base de tenant REAL: costo promedio, stock nunca
negativo (tambien bajo concurrencia), idempotencia, transferencias,
reversiones e inmutabilidad del libro."""

import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services import inventory
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database
from app.services.tenant_db_manager import tenant_db_manager

PASSWORD = "ClaveDePruebaSegura123"


@pytest.fixture(scope="module")
def inv():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-inv-{sfx}", name="Inv", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"inv-{sfx}", legal_name="Inv SRL", display_name="Inv", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    admin = User(email=f"inv-admin-{sfx}@example.com", password_hash=hash_password(PASSWORD),
                 role=Role.CLIENT_USER, tenant_id=tenant.id)
    viewer = User(email=f"inv-viewer-{sfx}@example.com", password_hash=hash_password(PASSWORD),
                  role=Role.CLIENT_USER, tenant_id=tenant.id)
    db.add_all([admin, viewer])
    db.commit()
    db.add_all([
        TenantUser(tenant_id=tenant.id, user_id=admin.id, role=TenantMemberRole.CLIENT_ADMIN, status=TenantMemberStatus.ACTIVE),
        TenantUser(tenant_id=tenant.id, user_id=viewer.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE),
    ])
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.PRODUCTION,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add(access)
    db.commit()
    tdb = provision_tenant_database(db, tenant.id, system.id, Environment.PRODUCTION)
    engine = tenant_db_manager.get_engine(tdb, tdb.credential)

    def login(email):
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
        return c

    c = login(admin.email)
    base = f"/api/erp/{access.id}"
    branch = c.post(f"{base}/branches", json={"code": "B1", "name": "Central"}).json()
    w1 = c.post(f"{base}/warehouses", json={"code": "W1", "name": "Uno", "branch_id": branch["id"]}).json()["id"]
    w2 = c.post(f"{base}/warehouses", json={"code": "W2", "name": "Dos", "branch_id": branch["id"]}).json()["id"]
    unit = next(u["id"] for u in c.get(f"{base}/units").json() if u["code"] == "UN")

    def product(sku, ptype="GOOD"):
        r = c.post(f"{base}/products", json={"sku": sku, "name": sku, "product_type": ptype, "unit_id": unit,
                                             "tax_code": "IVA10", "sale_price": "10000"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    yield {"c": c, "viewer": login(viewer.email), "s": f"{base}/stock", "w1": w1, "w2": w2,
           "product": product, "engine": engine, "admin_id": admin.id}

    force_drop_tenant_database_for_tests(tdb.database_identifier)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
    db.query(UserSession).filter(UserSession.user_id.in_([admin.id, viewer.id])).delete(synchronize_session=False)
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
    db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_([admin.id, viewer.id])).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def _balance(inv, pid, wid):
    r = inv["c"].get(f"{inv['s']}/balances", params={"product_id": pid, "warehouse_id": wid}).json()
    return Decimal(r["items"][0]["on_hand"]) if r["items"] else Decimal("0")


def _avg(inv, pid):
    with inv["engine"].connect() as conn:
        return conn.execute(text("SELECT average_cost FROM products WHERE id = :p"), {"p": pid}).scalar()


def test_receipts_compute_weighted_average_and_issue_uses_it(inv):
    c, s, w1, pid = inv["c"], inv["s"], inv["w1"], inv["product"]("AVG")
    assert c.post(f"{s}/receipts", json={"product_id": pid, "warehouse_id": w1, "quantity": "10", "unit_cost": "1000"}).status_code == 201
    assert c.post(f"{s}/receipts", json={"product_id": pid, "warehouse_id": w1, "quantity": "10", "unit_cost": "2000"}).status_code == 201
    assert _avg(inv, pid) == Decimal("1500.0000")
    out = c.post(f"{s}/issues", json={"product_id": pid, "warehouse_id": w1, "quantity": "5"})
    assert out.status_code == 201
    assert Decimal(out.json()[0]["unit_cost"]) == Decimal("1500")
    assert _balance(inv, pid, w1) == Decimal("15")
    bal = c.get(f"{s}/balances", params={"product_id": pid}).json()["items"][0]
    assert Decimal(bal["stock_value"]) == Decimal("22500.00")


def test_cannot_issue_more_than_available(inv):
    c, s, w1, pid = inv["c"], inv["s"], inv["w1"], inv["product"]("NEG")
    c.post(f"{s}/receipts", json={"product_id": pid, "warehouse_id": w1, "quantity": "3", "unit_cost": "100"})
    r = c.post(f"{s}/issues", json={"product_id": pid, "warehouse_id": w1, "quantity": "4"})
    assert r.status_code == 409
    assert _balance(inv, pid, w1) == Decimal("3")


def test_idempotency_key_prevents_double_issue(inv):
    c, s, w1, pid = inv["c"], inv["s"], inv["w1"], inv["product"]("IDEM")
    c.post(f"{s}/receipts", json={"product_id": pid, "warehouse_id": w1, "quantity": "10", "unit_cost": "100"})
    body = {"product_id": pid, "warehouse_id": w1, "quantity": "4"}
    first = c.post(f"{s}/issues", json=body, headers={"Idempotency-Key": "venta-123"})
    retry = c.post(f"{s}/issues", json=body, headers={"Idempotency-Key": "venta-123"})
    assert first.status_code == 201 and retry.status_code == 201
    assert first.json()[0]["id"] == retry.json()[0]["id"]
    assert _balance(inv, pid, w1) == Decimal("6")


def test_transfer_and_reverse_transfer(inv):
    c, s, w1, w2, pid = inv["c"], inv["s"], inv["w1"], inv["w2"], inv["product"]("TRF")
    c.post(f"{s}/receipts", json={"product_id": pid, "warehouse_id": w1, "quantity": "10", "unit_cost": "700"})
    t = c.post(f"{s}/transfers", json={"product_id": pid, "from_warehouse_id": w1, "to_warehouse_id": w2, "quantity": "4"})
    assert t.status_code == 201, t.text
    legs = t.json()
    assert len(legs) == 2 and legs[0]["group_id"] == legs[1]["group_id"]
    assert (_balance(inv, pid, w1), _balance(inv, pid, w2)) == (Decimal("6"), Decimal("4"))
    assert _avg(inv, pid) == Decimal("700.0000")
    assert c.post(f"{s}/transfers", json={"product_id": pid, "from_warehouse_id": w1, "to_warehouse_id": w1,
                                         "quantity": "1"}).status_code == 422

    rev = c.post(f"{s}/movements/{legs[1]['id']}/reverse", json={"reason": "transferencia equivocada"})
    assert rev.status_code == 201 and len(rev.json()) == 2
    assert (_balance(inv, pid, w1), _balance(inv, pid, w2)) == (Decimal("10"), Decimal("0"))
    again = c.post(f"{s}/movements/{legs[0]['id']}/reverse", json={"reason": "de nuevo"})
    assert again.status_code == 409


def test_reversal_rules(inv):
    c, s, w1, pid = inv["c"], inv["s"], inv["w1"], inv["product"]("REV")
    rec = c.post(f"{s}/receipts", json={"product_id": pid, "warehouse_id": w1, "quantity": "5", "unit_cost": "100"}).json()[0]
    c.post(f"{s}/issues", json={"product_id": pid, "warehouse_id": w1, "quantity": "3"})
    # Ese stock ya se uso: no se puede deshacer la entrada.
    assert c.post(f"{s}/movements/{rec['id']}/reverse", json={"reason": "error"}).status_code == 409

    iss = c.post(f"{s}/issues", json={"product_id": pid, "warehouse_id": w1, "quantity": "1"}).json()[0]
    r = c.post(f"{s}/movements/{iss['id']}/reverse", json={"reason": "devolucion"})
    assert r.status_code == 201
    assert _balance(inv, pid, w1) == Decimal("2")
    assert c.post(f"{s}/movements/{r.json()[0]['id']}/reverse", json={"reason": "x"}).status_code == 422


def test_adjustment_requires_reason_and_services_have_no_stock(inv):
    c, s, w1 = inv["c"], inv["s"], inv["w1"]
    pid = inv["product"]("ADJ")
    assert c.post(f"{s}/adjustments", json={"product_id": pid, "warehouse_id": w1, "direction": "IN",
                                           "quantity": "2"}).status_code == 422
    ok = c.post(f"{s}/adjustments", json={"product_id": pid, "warehouse_id": w1, "direction": "IN",
                                         "quantity": "2", "unit_cost": "50", "reason": "inventario fisico"})
    assert ok.status_code == 201 and ok.json()[0]["movement_type"] == "ADJUSTMENT_IN"
    srv = inv["product"]("SRV", ptype="SERVICE")
    assert c.post(f"{s}/receipts", json={"product_id": srv, "warehouse_id": w1, "quantity": "1",
                                        "unit_cost": "1"}).status_code == 422


def test_viewer_reads_but_cannot_move_stock(inv):
    pid = inv["product"]("VIEW")
    assert inv["viewer"].get(f"{inv['s']}/balances").status_code == 200
    assert inv["viewer"].post(f"{inv['s']}/receipts", json={"product_id": pid, "warehouse_id": inv["w1"],
                                                          "quantity": "1", "unit_cost": "1"}).status_code == 403


def test_kardex_filters_and_pagination(inv):
    c, s, w1, pid = inv["c"], inv["s"], inv["w1"], inv["product"]("KDX")
    for _ in range(3):
        c.post(f"{s}/receipts", json={"product_id": pid, "warehouse_id": w1, "quantity": "1", "unit_cost": "10"})
    page = c.get(f"{s}/movements", params={"product_id": pid, "limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2


def test_movements_are_immutable_at_database_level(inv):
    with inv["engine"].connect() as conn:
        mid = conn.execute(text("SELECT id FROM stock_movements LIMIT 1")).scalar()
    for stmt in ("UPDATE stock_movements SET quantity = 999 WHERE id = :m", "DELETE FROM stock_movements WHERE id = :m"):
        with pytest.raises(DBAPIError):
            with inv["engine"].begin() as conn:
                conn.execute(text(stmt), {"m": mid})


def test_concurrent_issues_never_oversell(inv):
    c, s, w1 = inv["c"], inv["s"], inv["w1"]
    pid = inv["product"]("CONC")
    c.post(f"{s}/receipts", json={"product_id": pid, "warehouse_id": w1, "quantity": "20", "unit_cost": "100"})

    results = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        with Session(bind=inv["engine"]) as db:
            try:
                inventory.issue(inventory.OpContext(db=db, user_id=inv["admin_id"]), uuid.UUID(pid), uuid.UUID(w1), Decimal("3"))
                db.commit()
                results.append("ok")
            except inventory.InsufficientStock:
                db.rollback()
                results.append("insufficient")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 6
    assert results.count("insufficient") == 4
    assert _balance(inv, pid, w1) == Decimal("2")
