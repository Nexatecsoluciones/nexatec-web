"""Notas de credito (devoluciones y bonificaciones) sobre una base REAL."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
def cn():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-cn-{sfx}", name="CN", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"cn-{sfx}", legal_name="CN SRL", display_name="CN", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    admin = User(email=f"cn-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
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
    pid = c.post(f"{base}/products", json={"sku": "CN-1", "name": "Producto CN", "product_type": "GOOD", "unit_id": un,
                                           "tax_code": "IVA10", "sale_price": "11000"}).json()["id"]
    c.post(f"{base}/stock/receipts", json={"product_id": pid, "warehouse_id": wh, "quantity": "100", "unit_cost": "5000"})
    cust = c.post(f"{base}/parties", json={"legal_name": "Cliente CN", "is_customer": True}).json()["id"]

    def invoice(qty):
        o = c.post(f"{base}/sales/orders", json={"customer_id": cust, "warehouse_id": wh, "payment_condition": "CASH",
                                                 "lines": [{"product_id": pid, "quantity": qty}]}).json()
        c.post(f"{base}/sales/orders/{o['id']}/confirm")
        c.post(f"{base}/sales/orders/{o['id']}/deliver")
        return c.post(f"{base}/sales/orders/{o['id']}/invoice").json()

    yield {"c": c, "base": base, "pid": pid, "cust": cust, "invoice": invoice,
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


def _stock(cn):
    return D(cn["c"].get(f"{cn['base']}/stock/balances", params={"product_id": cn["pid"]}).json()["items"][0]["on_hand"])


def _balance(cn, code):
    tb = cn["c"].get(f"{cn['base']}/accounting/reports/trial-balance", params={"date_from": "2000-01-01"}).json()
    assert D(tb["total_debit"]) == D(tb["total_credit"])
    return next((D(r["closing_balance"]) for r in tb["rows"] if r["code"] == code), D(0))


def test_return_discount_and_limits(cn):
    c, base = cn["c"], cn["base"]
    inv = cn["invoice"]("10")  # 110.000 (neto 100.000 + IVA 10.000), costo 10 x 5.000
    stock0, cogs0 = _stock(cn), _balance(cn, "5.1.01")

    r = c.post(f"{base}/invoices/{inv['id']}/credit-notes", json={"kind": "RETURN", "reason": "cliente devolvio 3",
                                                                 "restock": True, "lines": [{"line_no": 1, "quantity": "3"}]})
    assert r.status_code == 201, r.text
    n1 = r.json()
    assert n1["number"].startswith("NC-") and n1["legal_notice"]
    assert (D(n1["total"]), D(n1["taxable_10"]), D(n1["vat_10"])) == (D("33000"), D("30000"), D("3000"))
    assert D(n1["applied_amount"]) == D("33000") and D(n1["unapplied_amount"]) == 0
    assert D(c.get(f"{base}/invoices/{inv['id']}").json()["balance_due"]) == D("77000")
    assert _stock(cn) == stock0 + 3
    assert _balance(cn, "5.1.01") == cogs0 - D("15000")  # se revierte el costo (3 x 5.000)
    mov = next(m for m in c.get(f"{base}/stock/movements", params={"product_id": cn["pid"]}).json()["items"]
               if m["reference"] == n1["number"])
    assert c.post(f"{base}/stock/movements/{mov['id']}/reverse", json={"reason": "intento"}).status_code == 422

    assert c.post(f"{base}/invoices/{inv['id']}/credit-notes", json={"kind": "RETURN", "reason": "de mas",
                  "lines": [{"line_no": 1, "quantity": "8"}]}).status_code == 409

    d = c.post(f"{base}/invoices/{inv['id']}/credit-notes", json={"kind": "DISCOUNT", "reason": "bonificacion",
               "lines": [{"line_no": 1, "amount": "5000"}]}).json()
    assert (D(d["taxable_10"]), D(d["vat_10"])) == (D("4545"), D("455"))

    rest = c.post(f"{base}/invoices/{inv['id']}/credit-notes", json={"kind": "RETURN", "reason": "devuelve todo, roto",
                  "restock": False, "lines": [{"line_no": 1, "quantity": "7"}]}).json()
    assert D(rest["total"]) == D("72000")  # exactamente lo que quedaba (110.000 - 33.000 - 5.000)
    assert rest["restocked"] is False and _stock(cn) == stock0 + 3
    assert D(c.get(f"{base}/invoices/{inv['id']}").json()["balance_due"]) == 0
    assert c.post(f"{base}/invoices/{inv['id']}/credit-notes", json={"kind": "DISCOUNT", "reason": "otra",
                  "lines": [{"line_no": 1, "amount": "1"}]}).status_code == 409
    assert c.post(f"{base}/invoices/{inv['id']}/void", json={"reason": "no se puede"}).status_code == 409
    assert c.get(f"{base}/credit-notes", params={"invoice_id": inv["id"]}).json()["total"] == 3


def test_credit_on_paid_invoice_becomes_customer_credit(cn):
    c, base = cn["c"], cn["base"]
    inv = cn["invoice"]("2")  # 22.000
    c.post(f"{base}/receipts", json={"customer_id": cn["cust"], "method": "CASH", "amount": "22000",
                                     "allocations": [{"invoice_id": inv["id"], "amount": "22000"}]})
    n = c.post(f"{base}/invoices/{inv['id']}/credit-notes", json={"kind": "RETURN", "reason": "devolucion",
               "lines": [{"line_no": 1, "quantity": "1"}]}).json()
    assert D(n["applied_amount"]) == 0 and D(n["unapplied_amount"]) == D("11000")
    row = next(r for r in c.get(f"{base}/receivables/aging").json() if r["customer_id"] == cn["cust"])
    assert D(row["unapplied_advances"]) >= D("11000")
    kinds = [e["kind"] for e in c.get(f"{base}/receivables/customers/{cn['cust']}/statement").json()]
    assert "CREDIT_NOTE" in kinds


def test_database_guards_credit_limits_and_fiscal_gate(cn):
    with pytest.raises(IntegrityError):
        with cn["engine"].begin() as conn:
            conn.execute(text("UPDATE sales_order_lines SET amount_credited = line_total + 1"))
    with pytest.raises(IntegrityError):
        with cn["engine"].begin() as conn:
            conn.execute(text("UPDATE sales_credit_notes SET fiscal_status = 'APPROVED'"))
