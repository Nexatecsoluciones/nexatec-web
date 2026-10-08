"""Compras y cuentas por pagar sobre una base REAL."""

import uuid
from datetime import date, datetime, timedelta, timezone
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
    system = System(slug=f"pytest-ap-{sfx}", name="AP", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"ap-{sfx}", legal_name="AP SRL", display_name="AP", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    admin = User(email=f"ap-admin-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
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

    def product(tax="IVA10"):
        return c.post(f"{base}/products", json={"sku": f"P-{uuid.uuid4().hex[:6]}", "name": "x", "product_type": "GOOD",
                                               "unit_id": un, "tax_code": tax, "sale_price": "1"}).json()["id"]

    def supplier(terms=0):
        return c.post(f"{base}/parties", json={"legal_name": f"Prov {uuid.uuid4().hex[:6]}", "is_supplier": True,
                                              "payment_terms_days": terms}).json()["id"]

    def po(sup, lines, confirm=True):
        r = c.post(f"{base}/purchases/orders", json={"supplier_id": sup, "warehouse_id": wh, "lines": lines})
        assert r.status_code == 201, r.text
        if confirm:
            assert c.post(f"{base}/purchases/orders/{r.json()['id']}/confirm").status_code == 200
        return r.json()

    yield {"c": c, "base": base, "wh": wh, "product": product, "supplier": supplier, "po": po}

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


def _stock(p, pid):
    items = p["c"].get(f"{p['base']}/stock/balances", params={"product_id": pid}).json()["items"]
    return D(items[0]["on_hand"]) if items else D(0)


def _receive(p, po_id, line_no, qty, key=None):
    return p["c"].post(f"{p['base']}/purchases/orders/{po_id}/receive",
                       json={"lines": [{"line_no": line_no, "quantity": qty}]},
                       headers={"Idempotency-Key": key} if key else {})


def test_partial_and_full_receipt_at_net_cost(p):
    c, base = p["c"], p["base"]
    pid = p["product"]()
    o = p["po"](p["supplier"](), [{"product_id": pid, "quantity": "10", "unit_price": "11000"}])
    assert D(o["total"]) == D("110000") and D(o["tax_total"]) == D("10000")

    r1 = _receive(p, o["id"], 1, "4")
    assert r1.status_code == 200 and r1.json()["status"] == "PARTIALLY_RECEIVED"
    assert _stock(p, pid) == D("4")
    mov = c.get(f"{base}/stock/movements", params={"product_id": pid}).json()["items"][0]
    assert D(mov["unit_cost"]) == D("10000") and mov["reference"] == o["number"]  # costo NETO de IVA

    assert _receive(p, o["id"], 1, "7").status_code == 409
    assert _stock(p, pid) == D("4")
    r2 = _receive(p, o["id"], 1, "6")
    assert r2.json()["status"] == "RECEIVED" and _stock(p, pid) == D("10")
    assert _receive(p, o["id"], 1, "1").status_code == 409


def test_receive_idempotency(p):
    pid = p["product"]()
    o = p["po"](p["supplier"](), [{"product_id": pid, "quantity": "5", "unit_price": "1000"}])
    assert _receive(p, o["id"], 1, "2", key="rec-1").status_code == 200
    assert _receive(p, o["id"], 1, "2", key="rec-1").status_code == 200
    assert _stock(p, pid) == D("2")


def test_cancel_and_close(p):
    c, base = p["c"], p["base"]
    pid = p["product"]()
    untouched = p["po"](p["supplier"](), [{"product_id": pid, "quantity": "5", "unit_price": "1000"}])
    assert c.post(f"{base}/purchases/orders/{untouched['id']}/close", json={"reason": "ya no hace falta"}).json()["status"] == "CANCELLED"

    partial = p["po"](p["supplier"](), [{"product_id": pid, "quantity": "5", "unit_price": "1000"}])
    _receive(p, partial["id"], 1, "2")
    closed = c.post(f"{base}/purchases/orders/{partial['id']}/close", json={"reason": "proveedor sin stock"})
    assert closed.json()["status"] == "CLOSED"
    assert _receive(p, partial["id"], 1, "1").status_code == 409

    draft = p["po"](p["supplier"](), [{"product_id": pid, "quantity": "1", "unit_price": "1"}], confirm=False)
    assert _receive(p, draft["id"], 1, "1").status_code == 409


def test_supplier_invoice_controls(p):
    c, base = p["c"], p["base"]
    sup, other = p["supplier"](terms=30), p["supplier"]()
    pid = p["product"]()
    o = p["po"](sup, [{"product_id": pid, "quantity": "10", "unit_price": "11000"}])
    _receive(p, o["id"], 1, "4")  # recibido: 44.000

    body = {"supplier_id": sup, "supplier_invoice_number": "001-001-0000123", "supplier_timbrado": "12345678",
            "issue_date": date.today().isoformat(), "purchase_order_id": o["id"], "taxable_10": "100000", "vat_10": "10000"}
    assert c.post(f"{base}/supplier-invoices", json=body).status_code == 409  # supera lo recibido
    bad_vat = {**body, "taxable_10": "40000", "vat_10": "9000"}
    assert c.post(f"{base}/supplier-invoices", json=bad_vat).status_code == 422
    ok = c.post(f"{base}/supplier-invoices", json={**body, "taxable_10": "40000", "vat_10": "4000"})
    assert ok.status_code == 201, ok.text
    inv = ok.json()
    assert D(inv["total"]) == D("44000")
    assert date.fromisoformat(inv["due_date"]) - date.fromisoformat(inv["issue_date"]) == timedelta(days=30)

    dup = {**body, "purchase_order_id": None, "taxable_10": "1000", "vat_10": "100"}
    assert c.post(f"{base}/supplier-invoices", json=dup).status_code == 409  # duplicado mismo proveedor
    assert c.post(f"{base}/supplier-invoices", json={**dup, "supplier_id": other}).status_code == 201

    customer = c.post(f"{base}/parties", json={"legal_name": "Solo cliente", "is_customer": True}).json()["id"]
    assert c.post(f"{base}/supplier-invoices", json={**dup, "supplier_id": customer,
                                                     "supplier_invoice_number": "X-1"}).status_code == 422


def test_payments_and_aging(p):
    c, base = p["c"], p["base"]
    sup = p["supplier"](terms=30)
    inv = c.post(f"{base}/supplier-invoices", json={
        "supplier_id": sup, "supplier_invoice_number": "002-001-0000001", "issue_date": date.today().isoformat(),
        "taxable_10": "100000", "vat_10": "10000"}).json()

    pay = c.post(f"{base}/supplier-payments", json={"supplier_id": sup, "method": "TRANSFER", "amount": "50000",
                                                    "allocations": [{"invoice_id": inv["id"], "amount": "50000"}]},
                 headers={"Idempotency-Key": "pago-1"})
    again = c.post(f"{base}/supplier-payments", json={"supplier_id": sup, "method": "TRANSFER", "amount": "50000",
                                                      "allocations": [{"invoice_id": inv["id"], "amount": "50000"}]},
                   headers={"Idempotency-Key": "pago-1"})
    assert pay.status_code == 201 and again.json()["id"] == pay.json()["id"]
    assert D(c.get(f"{base}/supplier-invoices/{inv['id']}").json()["balance_due"]) == D("60000")

    over = c.post(f"{base}/supplier-payments", json={"supplier_id": sup, "method": "CASH", "amount": "70000",
                                                     "allocations": [{"invoice_id": inv["id"], "amount": "70000"}]})
    assert over.status_code == 409

    aging = c.get(f"{base}/payables/aging", params={"as_of": (date.today() + timedelta(days=40)).isoformat()}).json()
    row = next(r for r in aging if r["supplier_id"] == sup)
    assert D(row["d1_30"]) == D("60000")

    assert c.post(f"{base}/supplier-invoices/{inv['id']}/void", json={"reason": "error"}).status_code == 409
    assert c.post(f"{base}/supplier-payments/{pay.json()['id']}/void", json={"reason": "transferencia rebotada"}).status_code == 200
    assert D(c.get(f"{base}/supplier-invoices/{inv['id']}").json()["balance_due"]) == D("110000")
    assert c.post(f"{base}/supplier-invoices/{inv['id']}/void", json={"reason": "factura mal cargada"}).json()["status"] == "VOIDED"


def test_po_receipt_movement_cannot_be_reversed_directly(p):
    c, base = p["c"], p["base"]
    pid = p["product"]()
    o = p["po"](p["supplier"](), [{"product_id": pid, "quantity": "3", "unit_price": "1100"}])
    _receive(p, o["id"], 1, "3")
    mov = c.get(f"{base}/stock/movements", params={"product_id": pid}).json()["items"][0]
    assert c.post(f"{base}/stock/movements/{mov['id']}/reverse", json={"reason": "intento"}).status_code == 422
    assert _stock(p, pid) == D("3")
