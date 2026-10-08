"""Contabilidad sobre una base REAL: asientos automaticos de todo el ciclo
compra -> venta -> cobro con montos exactos, garantias de la base (partida
doble, inmutabilidad), periodo cerrado bloqueando la operacion entera, e
informes que cuadran."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
def a():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-gl-{sfx}", name="GL", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"gl-{sfx}", legal_name="GL SRL", display_name="GL", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    admin = User(email=f"gl-admin-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
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
    accounts = {x["code"]: x for x in c.get(f"{base}/accounting/accounts").json()}

    yield {"c": c, "base": base, "wh": wh, "un": un, "acc": accounts,
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


def _balances(a) -> dict[str, Decimal]:
    tb = a["c"].get(f"{a['base']}/accounting/reports/trial-balance",
                    params={"date_from": "2000-01-01", "date_to": (date.today() + timedelta(days=1)).isoformat()}).json()
    assert D(tb["total_debit"]) == D(tb["total_credit"])
    return {r["code"]: D(r["closing_balance"]) for r in tb["rows"]}


def test_full_cycle_posts_exact_entries(a):
    c, base = a["c"], a["base"]
    pid = c.post(f"{base}/products", json={"sku": "GL-1", "name": "Producto", "product_type": "GOOD", "unit_id": a["un"],
                                           "tax_code": "IVA10", "sale_price": "22000"}).json()["id"]
    sup = c.post(f"{base}/parties", json={"legal_name": "Proveedor", "is_supplier": True}).json()["id"]
    cust = c.post(f"{base}/parties", json={"legal_name": "Cliente", "is_customer": True}).json()["id"]

    po = c.post(f"{base}/purchases/orders", json={"supplier_id": sup, "warehouse_id": a["wh"],
                                                  "lines": [{"product_id": pid, "quantity": "10", "unit_price": "11000"}]}).json()
    c.post(f"{base}/purchases/orders/{po['id']}/confirm")
    assert c.post(f"{base}/purchases/orders/{po['id']}/receive", json={"lines": [{"line_no": 1, "quantity": "10"}]}).status_code == 200
    sinv = c.post(f"{base}/supplier-invoices", json={"supplier_id": sup, "supplier_invoice_number": "001-001-1",
                                                     "issue_date": date.today().isoformat(), "purchase_order_id": po["id"],
                                                     "taxable_10": "100000", "vat_10": "10000"})
    assert sinv.status_code == 201, sinv.text
    assert c.post(f"{base}/supplier-payments", json={"supplier_id": sup, "method": "TRANSFER", "amount": "110000",
                                                     "allocations": [{"invoice_id": sinv.json()["id"], "amount": "110000"}]}).status_code == 201

    so = c.post(f"{base}/sales/orders", json={"customer_id": cust, "warehouse_id": a["wh"], "payment_condition": "CASH",
                                              "lines": [{"product_id": pid, "quantity": "4"}]}).json()
    c.post(f"{base}/sales/orders/{so['id']}/confirm")
    c.post(f"{base}/sales/orders/{so['id']}/deliver")
    inv = c.post(f"{base}/sales/orders/{so['id']}/invoice").json()
    rc = c.post(f"{base}/receipts", json={"customer_id": cust, "method": "CASH", "amount": "100000",
                                          "allocations": [{"invoice_id": inv["id"], "amount": "88000"}]})
    assert rc.status_code == 201

    b = _balances(a)
    expected = {
        "1.1.01": D("100000"),   # Caja
        "1.1.02": D("-110000"),  # Bancos (pago al proveedor)
        "1.1.05": D("10000"),    # IVA credito fiscal
        "1.1.06": D("60000"),    # Mercaderias: 100.000 - 40.000
        "2.1.02": D("-12000"),   # Anticipo del cliente (lo cobrado de mas)
        "2.1.03": D("-8000"),    # IVA debito 10%
        "4.1.01": D("-80000"),   # Ventas netas
        "5.1.01": D("40000"),    # Costo de ventas: 4 x 10.000 (costo neto de IVA)
    }
    for code, value in expected.items():
        assert b.get(code) == value, (code, b.get(code))
    for cleared in ("1.1.03", "2.1.01", "2.1.05"):  # deudores, proveedores, mercaderias a facturar
        assert b.get(cleared, D(0)) == 0, cleared

    pl = c.get(f"{base}/accounting/reports/income-statement", params={"date_from": "2000-01-01"}).json()
    assert (D(pl["total_income"]), D(pl["total_expense"]), D(pl["net_result"])) == (D("80000"), D("40000"), D("40000"))
    bs = c.get(f"{base}/accounting/reports/balance-sheet").json()
    assert bs["balanced"] is True and D(bs["total_assets"]) == D("60000")
    assert "gestion" in bs["notice"]

    # Anular el cobro revierte su asiento: vuelve la deuda del cliente.
    assert c.post(f"{base}/receipts/{rc.json()['id']}/void", json={"reason": "billete falso"}).status_code == 200
    b = _balances(a)
    assert b["1.1.03"] == D("88000") and b["1.1.01"] == 0 and b["2.1.02"] == 0


def test_every_entry_has_a_source_document(a):
    entries = a["c"].get(f"{a['base']}/accounting/entries", params={"limit": 200}).json()["items"]
    kinds = {e["source_type"] for e in entries}
    assert {"STOCK_MOVEMENT", "SUPPLIER_INVOICE", "SUPPLIER_PAYMENT", "SALES_INVOICE", "CUSTOMER_RECEIPT"} <= kinds
    for e in entries:
        assert sum(D(l["debit"]) for l in e["lines"]) == sum(D(l["credit"]) for l in e["lines"])


def test_database_enforces_double_entry_and_immutability(a):
    eng = a["engine"]
    with eng.connect() as conn:
        entry_id = conn.execute(text("SELECT id FROM journal_entries LIMIT 1")).scalar()
        period_id = conn.execute(text("SELECT period_id FROM journal_entries LIMIT 1")).scalar()
        acc = conn.execute(text("SELECT id FROM accounts WHERE code='1.1.01'")).scalar()
    with pytest.raises(DBAPIError):
        with eng.begin() as conn:
            conn.execute(text("UPDATE journal_lines SET debit = debit + 1 WHERE entry_id = :e"), {"e": entry_id})
    with pytest.raises(DBAPIError):
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM journal_entries WHERE id = :e"), {"e": entry_id})
    new_id = uuid.uuid4()
    with pytest.raises(DBAPIError):  # desbalanceado: falla al COMMIT por el trigger diferido
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO journal_entries (id, number, entry_date, period_id, description, source_type) "
                              "VALUES (:i, 'X-1', current_date, :p, 'hack', 'MANUAL')"), {"i": new_id, "p": period_id})
            conn.execute(text("INSERT INTO journal_lines (id, entry_id, line_no, account_id, debit, credit) "
                              "VALUES (gen_random_uuid(), :i, 1, :a, 100, 0), (gen_random_uuid(), :i, 2, :a, 0, 90)"),
                         {"i": new_id, "a": acc})


def test_manual_entries_and_reversal_rules(a):
    c, base, acc = a["c"], a["base"], a["acc"]
    today = date.today().isoformat()
    unbalanced = c.post(f"{base}/accounting/entries", json={"entry_date": today, "description": "aporte", "lines": [
        {"account_id": acc["1.1.01"]["id"], "debit": "1000"}, {"account_id": acc["3.1.01"]["id"], "credit": "900"}]})
    assert unbalanced.status_code == 422
    group = c.post(f"{base}/accounting/entries", json={"entry_date": today, "description": "aporte", "lines": [
        {"account_id": acc["1"]["id"], "debit": "1000"}, {"account_id": acc["3.1.01"]["id"], "credit": "1000"}]})
    assert group.status_code == 422  # cuenta de agrupacion, no imputable
    ok = c.post(f"{base}/accounting/entries", json={"entry_date": today, "description": "Aporte de capital", "lines": [
        {"account_id": acc["1.1.01"]["id"], "debit": "500000"}, {"account_id": acc["3.1.01"]["id"], "credit": "500000"}]})
    assert ok.status_code == 201, ok.text
    rev = c.post(f"{base}/accounting/entries/{ok.json()['id']}/reverse", json={"reason": "cargado dos veces"})
    assert rev.status_code == 201 and rev.json()["reverses_entry_id"] == ok.json()["id"]
    assert c.post(f"{base}/accounting/entries/{ok.json()['id']}/reverse", json={"reason": "otra vez"}).status_code == 409
    auto = c.get(f"{base}/accounting/entries", params={"source_type": "SALES_INVOICE"}).json()["items"][0]
    assert c.post(f"{base}/accounting/entries/{auto['id']}/reverse", json={"reason": "no"}).status_code in (409, 422)


def test_closed_period_blocks_the_whole_operation(a):
    c, base = a["c"], a["base"]
    today = date.today()
    pid = c.post(f"{base}/products", json={"sku": "GL-CLOSE", "name": "x", "product_type": "GOOD", "unit_id": a["un"],
                                           "tax_code": "IVA10", "sale_price": "1"}).json()["id"]
    assert c.post(f"{base}/accounting/periods/{today.year}/{today.month}/close", json={"note": "cierre mensual"}).status_code == 200
    blocked = c.post(f"{base}/stock/receipts", json={"product_id": pid, "warehouse_id": a["wh"], "quantity": "5", "unit_cost": "100"})
    assert blocked.status_code == 409
    assert c.get(f"{base}/stock/balances", params={"product_id": pid}).json()["total"] == 0  # no entro stock sin asiento

    assert c.post(f"{base}/accounting/periods/{today.year}/{today.month}/reopen", json={"note": "ajuste pendiente"}).status_code == 200
    assert c.post(f"{base}/stock/receipts", json={"product_id": pid, "warehouse_id": a["wh"], "quantity": "5",
                                                 "unit_cost": "100"}).status_code == 201


def test_ledger_running_balance_and_pagination(a):
    c, base, acc = a["c"], a["base"], a["acc"]
    full = c.get(f"{base}/accounting/reports/ledger/{acc['1.1.06']['id']}", params={"date_from": "2000-01-01"}).json()
    assert full["total"] >= 3
    last = full["items"][-1]["balance"]
    page2 = c.get(f"{base}/accounting/reports/ledger/{acc['1.1.06']['id']}",
                  params={"date_from": "2000-01-01", "limit": 1, "offset": full["total"] - 1}).json()
    assert page2["items"][0]["balance"] == last


def test_mapping_rules(a):
    c, base, acc = a["c"], a["base"], a["acc"]
    assert c.put(f"{base}/accounting/mappings/CASH", json={"account_id": acc["1.1"]["id"]}).status_code == 422
    assert c.patch(f"{base}/accounting/accounts/{acc['1.1.01']['id']}", json={"is_active": False}).status_code == 409
    new = c.post(f"{base}/accounting/accounts", json={"code": "1.1.07", "name": "Caja chica", "account_type": "ASSET",
                                                      "parent_id": acc["1.1"]["id"]})
    assert new.status_code == 201
    assert c.put(f"{base}/accounting/mappings/CASH", json={"account_id": new.json()["id"]}).status_code == 200
    assert c.put(f"{base}/accounting/mappings/CASH", json={"account_id": acc["1.1.01"]["id"]}).status_code == 200
