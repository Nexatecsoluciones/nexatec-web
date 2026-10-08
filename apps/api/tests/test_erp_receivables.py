"""Facturas internas, cobros y cuentas por cobrar sobre una base REAL."""

import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services import receivables
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database
from app.services.tenant_db_manager import tenant_db_manager
from app.tenant_models.receivables import PaymentMethod

PASSWORD = "ClaveDePruebaSegura123"
D = Decimal


@pytest.fixture(scope="module")
def r():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-ar-{sfx}", name="AR", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"ar-{sfx}", legal_name="AR SRL", display_name="AR", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    admin = User(email=f"ar-admin-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
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
    base = f"/api/erp/{access.id}"
    branch = c.post(f"{base}/branches", json={"code": "B1", "name": "Central"}).json()
    wh = c.post(f"{base}/warehouses", json={"code": "W1", "name": "Uno", "branch_id": branch["id"]}).json()["id"]
    un = next(u["id"] for u in c.get(f"{base}/units").json() if u["code"] == "UN")

    def product(tax, price):
        pid = c.post(f"{base}/products", json={"sku": f"P-{uuid.uuid4().hex[:6]}", "name": "x", "product_type": "GOOD",
                                               "unit_id": un, "tax_code": tax, "sale_price": price}).json()["id"]
        assert c.post(f"{base}/stock/receipts", json={"product_id": pid, "warehouse_id": wh, "quantity": "1000",
                                                     "unit_cost": "100"}).status_code == 201
        return pid

    def customer(limit="0", terms=0):
        return c.post(f"{base}/parties", json={"legal_name": f"Cliente {uuid.uuid4().hex[:6]}", "is_customer": True,
                                              "credit_limit": limit, "payment_terms_days": terms}).json()["id"]

    def order(cust, lines, cond="CASH", deliver=True):
        o = c.post(f"{base}/sales/orders", json={"customer_id": cust, "warehouse_id": wh, "payment_condition": cond,
                                                 "lines": lines})
        assert o.status_code == 201, o.text
        oid = o.json()["id"]
        conf = c.post(f"{base}/sales/orders/{oid}/confirm")
        if deliver:
            assert conf.status_code == 200, conf.text
            assert c.post(f"{base}/sales/orders/{oid}/deliver").status_code == 200
        return oid, conf

    p10 = product("IVA10", "5000")
    yield {"c": c, "base": base, "product": product, "customer": customer, "order": order, "p10": p10,
           "engine": tenant_db_manager.get_engine(tdb, tdb.credential)}

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


def _invoice(r, oid):
    resp = r["c"].post(f"{r['base']}/sales/orders/{oid}/invoice")
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_invoice_vat_breakdown_and_simulation_notice(r):
    p5, p0 = r["product"]("IVA5", "10000"), r["product"]("EXENTA", "7000")
    oid, _ = r["order"](r["customer"](), [{"product_id": r["p10"], "quantity": "3"},
                                          {"product_id": p5, "quantity": "1"}, {"product_id": p0, "quantity": "1"}])
    inv = _invoice(r, oid)
    assert inv["number"].startswith("FI-")
    assert inv["fiscal_status"] == "INTERNAL_SIMULATION"
    assert inv["legal_notice"] == "DOCUMENTO DE SIMULACIÓN — SIN VALIDEZ TRIBUTARIA"
    got = {k: D(inv[k]) for k in ("taxable_10", "vat_10", "taxable_5", "vat_5", "exempt", "total", "balance_due")}
    assert got == {"taxable_10": D("13636"), "vat_10": D("1364"), "taxable_5": D("9524"), "vat_5": D("476"),
                   "exempt": D("7000"), "total": D("32000"), "balance_due": D("32000")}
    assert inv["due_date"] == inv["issue_date"]
    assert r["c"].post(f"{r['base']}/sales/orders/{oid}/invoice").status_code == 409


def test_cannot_invoice_undelivered(r):
    oid, _ = r["order"](r["customer"](), [{"product_id": r["p10"], "quantity": "1"}], deliver=False)
    assert r["c"].post(f"{r['base']}/sales/orders/{oid}/invoice").status_code == 409


def test_fiscal_gate_lives_in_database(r):
    with r["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM sales_invoices")).scalar() > 0
    with pytest.raises(IntegrityError):
        with r["engine"].begin() as conn:
            conn.execute(text("UPDATE sales_invoices SET fiscal_status = 'APPROVED'"))


def test_receipts_partial_overapply_advance_void(r):
    c, base = r["c"], r["base"]
    cust = r["customer"]()
    inv = _invoice(r, r["order"](cust, [{"product_id": r["p10"], "quantity": "2"}])[0])  # 10000

    rc1 = c.post(f"{base}/receipts", json={"customer_id": cust, "method": "CASH", "amount": "4000",
                                           "allocations": [{"invoice_id": inv["id"], "amount": "4000"}]})
    assert rc1.status_code == 201, rc1.text
    assert D(c.get(f"{base}/invoices/{inv['id']}").json()["balance_due"]) == D("6000")

    over = c.post(f"{base}/receipts", json={"customer_id": cust, "method": "CASH", "amount": "7000",
                                            "allocations": [{"invoice_id": inv["id"], "amount": "7000"}]})
    assert over.status_code == 409
    assert D(c.get(f"{base}/invoices/{inv['id']}").json()["balance_due"]) == D("6000")

    adv = c.post(f"{base}/receipts", json={"customer_id": cust, "method": "TRANSFER", "amount": "8000"})
    assert adv.status_code == 201 and D(adv.json()["unapplied_amount"]) == D("8000")
    applied = c.post(f"{base}/receipts/{adv.json()['id']}/apply", json={"allocations": [{"invoice_id": inv["id"], "amount": "6000"}]})
    assert applied.status_code == 200 and D(applied.json()["unapplied_amount"]) == D("2000")
    assert D(c.get(f"{base}/invoices/{inv['id']}").json()["balance_due"]) == D("0")

    assert c.post(f"{base}/invoices/{inv['id']}/void", json={"reason": "error"}).status_code == 409
    voided = c.post(f"{base}/receipts/{rc1.json()['id']}/void", json={"reason": "cheque rechazado"})
    assert voided.status_code == 200 and voided.json()["status"] == "VOIDED"
    assert D(c.get(f"{base}/invoices/{inv['id']}").json()["balance_due"]) == D("4000")

    stmt = c.get(f"{base}/receivables/customers/{cust}/statement").json()
    assert [row["kind"] for row in stmt] == ["INVOICE", "RECEIPT", "RECEIPT"]
    assert D(stmt[-1]["balance"]) == D("2000")  # 10000 - 8000 (el cobro anulado no cuenta)


def test_receipt_idempotency(r):
    c, base = r["c"], r["base"]
    cust = r["customer"]()
    body = {"customer_id": cust, "method": "CASH", "amount": "1500"}
    a = c.post(f"{base}/receipts", json=body, headers={"Idempotency-Key": "cobro-777"})
    b = c.post(f"{base}/receipts", json=body, headers={"Idempotency-Key": "cobro-777"})
    assert a.status_code == 201 and b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert c.get(f"{base}/receipts", params={"customer_id": cust}).json()["total"] == 1


def test_void_invoice_then_reinvoice(r):
    c, base = r["c"], r["base"]
    oid, _ = r["order"](r["customer"](), [{"product_id": r["p10"], "quantity": "1"}])
    inv = _invoice(r, oid)
    v = c.post(f"{base}/invoices/{inv['id']}/void", json={"reason": "datos del cliente mal"})
    assert v.status_code == 200 and v.json()["status"] == "VOIDED" and D(v.json()["balance_due"]) == 0
    again = _invoice(r, oid)
    assert again["id"] != inv["id"]


def test_credit_limit_and_terms(r):
    c, base = r["c"], r["base"]
    no_credit = r["customer"]()
    _, conf = r["order"](no_credit, [{"product_id": r["p10"], "quantity": "1"}], cond="CREDIT", deliver=False)
    assert conf.status_code == 409

    cust = r["customer"](limit="50000", terms=30)
    oid, conf = r["order"](cust, [{"product_id": r["p10"], "quantity": "8"}], cond="CREDIT")  # 40000
    inv = _invoice(r, oid)
    assert date.fromisoformat(inv["due_date"]) - date.fromisoformat(inv["issue_date"]) == timedelta(days=30)

    _, blocked = r["order"](cust, [{"product_id": r["p10"], "quantity": "4"}], cond="CREDIT", deliver=False)  # 20000
    assert blocked.status_code == 409 and "limite de credito" in blocked.json()["detail"]

    c.post(f"{base}/receipts", json={"customer_id": cust, "method": "CASH", "amount": "40000",
                                     "allocations": [{"invoice_id": inv["id"], "amount": "40000"}]})
    _, ok = r["order"](cust, [{"product_id": r["p10"], "quantity": "4"}], cond="CREDIT", deliver=False)
    assert ok.status_code == 200

    aging = c.get(f"{base}/receivables/aging", params={"as_of": (date.fromisoformat(inv["issue_date"]) + timedelta(days=45)).isoformat()}).json()
    assert all(row["customer_id"] != cust or D(row["total_due"]) == 0 for row in aging)


def test_aging_buckets(r):
    c, base = r["c"], r["base"]
    cust = r["customer"](limit="1000000", terms=30)
    inv = _invoice(r, r["order"](cust, [{"product_id": r["p10"], "quantity": "2"}], cond="CREDIT")[0])
    issue = date.fromisoformat(inv["issue_date"])

    def row(days):
        rows = c.get(f"{base}/receivables/aging", params={"as_of": (issue + timedelta(days=days)).isoformat()}).json()
        return next(x for x in rows if x["customer_id"] == cust)

    assert D(row(10)["current"]) == D("10000")
    assert D(row(45)["d1_30"]) == D("10000")
    assert D(row(130)["d90_plus"]) == D("10000")


def test_concurrent_receipts_never_overapply(r):
    cust = r["customer"]()
    inv = _invoice(r, r["order"](cust, [{"product_id": r["p10"], "quantity": "2"}])[0])  # 10000
    results, barrier = [], threading.Barrier(2)

    def worker():
        barrier.wait()
        with Session(bind=r["engine"]) as db:
            try:
                receivables.post_receipt(db, user_id=None, customer_id=uuid.UUID(cust), method=PaymentMethod.CASH,
                                         amount=D("6000"), allocations=[receivables.AllocationInput(uuid.UUID(inv["id"]), D("6000"))],
                                         reference=None, idempotency_key=None)
                db.commit()
                results.append("ok")
            except receivables.Conflict:
                db.rollback()
                results.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["conflict", "ok"]
    assert D(r["c"].get(f"{r['base']}/invoices/{inv['id']}").json()["balance_due"]) == D("4000")
