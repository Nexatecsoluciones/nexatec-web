"""Venta rapida, caja y codigos de barras sobre una base de DEMO REAL
(productos y stock de la empresa ficticia). Verifica la conciliacion
stock / caja / cuentas por cobrar / contabilidad, no solo respuestas HTTP."""

import threading
import uuid
from datetime import datetime, timedelta, timezone
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
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole as R, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services import pos
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database
from app.services.tenant_db_manager import tenant_db_manager

PASSWORD = "ClaveDePruebaSegura123"
D = Decimal


@pytest.fixture(scope="module")
def t():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-pos-{sfx}", name="POS", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"pos-{sfx}", legal_name="POS", display_name="POS", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.DEMO,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add(access)
    db.commit()
    tdb = provision_tenant_database(db, tenant.id, system.id, Environment.DEMO)  # con datos de demo
    users, clients = {}, {}
    for name, role in (("admin", R.CLIENT_ADMIN), ("cajero1", R.SALES), ("cajero2", R.SALES), ("gerente", R.MANAGER),
                       ("deposito", R.WAREHOUSE)):
        u = User(email=f"pos-{name}-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER,
                 tenant_id=tenant.id)
        db.add(u)
        db.commit()
        db.add(TenantUser(tenant_id=tenant.id, user_id=u.id, role=role, status=TenantMemberStatus.ACTIVE))
        db.commit()
        users[name] = u.id
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": u.email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
        clients[name] = c
    base = f"/api/erp/{access.id}"
    admin = clients["admin"]
    wh = next(w["id"] for w in admin.get(f"{base}/warehouses").json() if w["code"] == "DEP-01")
    products = {p["sku"]: p for p in admin.get(f"{base}/products", params={"limit": 100}).json()["items"]}
    reg1 = admin.post(f"{base}/pos/registers", json={"code": "c1", "name": "Caja 1", "warehouse_id": wh}).json()
    reg2 = admin.post(f"{base}/pos/registers", json={"code": "C2", "name": "Caja 2", "warehouse_id": wh}).json()
    engine = tenant_db_manager.get_engine(tdb, tdb.credential)
    yield {"c": clients, "base": base, "wh": wh, "p": products, "reg1": reg1["id"], "reg2": reg2["id"],
           "engine": engine, "users": users}
    force_drop_tenant_database_for_tests(tdb.database_identifier)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
    uids = list(users.values())
    db.query(UserSession).filter(UserSession.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
    db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
    db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def sale(c, base, lines, payments, condition="CASH", customer_id=None, key=None):
    return c.post(f"{base}/pos/sales", headers={"Idempotency-Key": key or uuid.uuid4().hex},
                  json={"lines": lines, "payments": payments, "condition": condition, "customer_id": customer_id})


def stock(t, sku):
    with t["engine"].connect() as conn:
        return conn.execute(text("SELECT on_hand - reserved FROM stock_balances b JOIN products p ON p.id = b.product_id "
                                 "WHERE p.sku = :s AND b.warehouse_id = :w"), {"s": sku, "w": t["wh"]}).scalar()


def gl_balanced(t) -> bool:
    with t["engine"].connect() as conn:
        return conn.execute(text("SELECT coalesce(sum(debit), 0) = coalesce(sum(credit), 0) FROM journal_lines")).scalar()


def test_barcodes_validation_and_lookup(t):
    c, base = t["c"]["admin"], t["base"]
    pid = t["p"]["BEB-AGUA-500"]["id"]
    assert c.post(f"{base}/products/{pid}/barcodes", json={"code": "7840000000003"}).status_code == 422  # DV malo
    assert c.post(f"{base}/products/{pid}/barcodes", json={"code": "7840000000005", "primary": True}).status_code == 201
    gen = c.post(f"{base}/products/{pid}/barcodes/generate").json()
    assert gen["code"].startswith("NX") and gen["symbology"] == "CODE128" and gen["is_internal"] and gen["is_primary"]
    codes = c.get(f"{base}/products/{pid}/barcodes").json()
    assert sum(1 for x in codes if x["is_primary"]) == 1 and len(codes) == 2
    other = t["p"]["BEB-JUGO-1L"]["id"]
    assert c.post(f"{base}/products/{other}/barcodes", json={"code": "7840000000005"}).status_code == 409
    assert c.get(f"{base}/pos/lookup", params={"code": "7840000000005"}).json()["sku"] == "BEB-AGUA-500"
    assert c.get(f"{base}/pos/lookup", params={"code": "beb-agua-500"}).json()["product_id"] == pid  # SKU
    assert c.get(f"{base}/pos/lookup", params={"code": "NO-EXISTE"}).json() is None
    lab = c.get(f"{base}/products-labels", params={"ids": [pid]}).json()[0]
    assert lab["code"] == gen["code"] and lab["symbology"] == "CODE128"


def test_full_cash_flow_reconciles(t):
    base, cj = t["base"], t["c"]["cajero1"]
    assert sale(cj, base, [{"product_id": t["p"]["BEB-AGUA-500"]["id"], "quantity": "1"}], []).status_code == 409  # sin caja
    op = cj.post(f"{base}/pos/sessions", json={"register_id": t["reg1"], "opening_float": "100000"})
    assert op.status_code == 201 and op.json()["session"]["number"].startswith("CJ-")
    sid = op.json()["session"]["id"]
    assert t["c"]["cajero2"].post(f"{base}/pos/sessions", json={"register_id": t["reg1"], "opening_float": "0"}).status_code == 409
    before = stock(t, "BEB-GASEOSA-2L")
    # 2 gaseosas (15.000) + 1 arroz (9.000, IVA 5) = 39.000; paga 20.000 tarjeta + 50.000 efectivo -> vuelto 31.000
    r = sale(cj, base, [{"product_id": t["p"]["BEB-GASEOSA-2L"]["id"], "quantity": "2"},
                        {"product_id": t["p"]["ALM-ARROZ-1KG"]["id"], "quantity": "1"}],
             [{"method": "CARD", "amount": "20000", "reference": "voucher 123"}, {"method": "CASH", "amount": "50000"}])
    assert r.status_code == 201, r.text
    s1 = r.json()
    assert D(s1["total"]) == 39000 and D(s1["paid"]) == 39000 and D(s1["change_given"]) == 31000
    assert s1["invoice_number"].startswith("FI-")
    assert stock(t, "BEB-GASEOSA-2L") == before - 2
    inv = cj.get(f"{base}/invoices/{s1['invoice_id']}") if False else None  # (cajero no lee facturas: no hace falta)
    admin = t["c"]["admin"]
    inv = admin.get(f"{base}/invoices/{s1['invoice_id']}").json()
    assert D(inv["balance_due"]) == 0 and inv["fiscal_status"] == "INTERNAL_SIMULATION"
    assert D(inv["vat_10"]) == D("2727") and D(inv["vat_5"]) == D("429")
    # Caja: esperado = 100.000 + 19.000 efectivo aplicado; un gasto de 5.000.
    cur = cj.get(f"{base}/pos/session/current").json()
    assert D(cur["summary"]["expected_cash"]) == 119000 and D(cur["summary"]["by_method"]["CARD"]) == 20000
    assert cj.post(f"{base}/pos/sessions/{sid}/movements", json={"kind": "EXPENSE", "amount": "5000",
                                                                "description": "Compra de bolsas"}).status_code == 201
    assert cj.post(f"{base}/pos/sessions/{sid}/movements", json={"kind": "EXPENSE", "amount": "999999",
                                                                "description": "Mas de lo que hay"}).status_code == 409
    # Cierre con faltante de 1.000: asiento de diferencia; el detalle de billetes tiene que cuadrar.
    bad = cj.post(f"{base}/pos/sessions/{sid}/close", json={"counted_cash": "113000", "count_detail": {"100000": 1}})
    assert bad.status_code == 422
    closed = cj.post(f"{base}/pos/sessions/{sid}/close", json={"counted_cash": "113000",
                                                               "count_detail": {"100000": 1, "10000": 1, "1000": 3}}).json()
    assert closed["session"]["status"] == "CLOSED" and D(closed["session"]["expected_cash"]) == 114000
    assert D(closed["session"]["difference"]) == -1000
    assert gl_balanced(t)
    with t["engine"].connect() as conn:
        short = conn.execute(text("SELECT coalesce(sum(l.debit), 0) FROM journal_lines l JOIN accounts a ON a.id = l.account_id "
                                  "WHERE a.code = '6.1.03'")).scalar()
        assert short == 1000
        # Turno cerrado: la base no deja tocarlo ni agregarle movimientos.
        with pytest.raises(DBAPIError):
            with t["engine"].begin() as c2:
                c2.execute(text("UPDATE cash_sessions SET counted_cash = 0 WHERE id = :s"), {"s": sid})
        with pytest.raises(DBAPIError):
            with t["engine"].begin() as c2:
                c2.execute(text("DELETE FROM cash_movements WHERE session_id = :s"), {"s": sid})
    assert sale(cj, base, [{"product_id": t["p"]["BEB-AGUA-500"]["id"], "quantity": "1"}],
                [{"method": "CASH", "amount": "5000"}]).status_code == 409  # turno cerrado


def test_idempotency_and_permissions(t):
    base, cj, ger = t["base"], t["c"]["cajero2"], t["c"]["gerente"]
    cj.post(f"{base}/pos/sessions", json={"register_id": t["reg2"], "opening_float": "0"})
    line = [{"product_id": t["p"]["BEB-JUGO-1L"]["id"], "quantity": "1"}]
    pay = [{"method": "CASH", "amount": "12000"}]
    key = uuid.uuid4().hex
    a, b = sale(cj, base, line, pay, key=key), sale(cj, base, line, pay, key=key)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"] and b.json()["replayed"]
    assert sale(cj, base, line, pay, key=None).status_code == 201  # clave nueva = venta nueva
    r = cj.post(f"{base}/pos/sales", json={"lines": line, "payments": pay})
    assert r.status_code == 422  # sin Idempotency-Key
    # Cajero no puede descontar ni cambiar precio; falta de pago en contado.
    assert sale(cj, base, [line[0] | {"discount_pct": "10"}], pay).status_code == 409
    assert sale(cj, base, [line[0] | {"unit_price": "1"}], [{"method": "CASH", "amount": "1"}]).status_code == 409
    assert sale(cj, base, line, [{"method": "CASH", "amount": "11000"}]).status_code == 422
    assert sale(cj, base, line, [{"method": "CARD", "amount": "13000"}]).status_code == 422
    # Deposito no vende; cajero no anula.
    assert t["c"]["deposito"].get(f"{base}/pos/search", params={"q": "agua"}).status_code == 403
    assert cj.post(f"{base}/pos/sales/{a.json()['id']}/void", json={"reason": "prueba"}).status_code == 403
    # Gerente: descuento permitido (en su propio turno).
    ger.post(f"{base}/pos/sessions", json={"register_id": t["reg1"], "opening_float": "0"})
    d = sale(ger, base, [line[0] | {"discount_pct": "10"}], [{"method": "CASH", "amount": "10800"}])
    assert d.status_code == 201 and D(d.json()["total"]) == 10800


def test_void_restores_everything(t):
    base, cj, ger = t["base"], t["c"]["cajero2"], t["c"]["gerente"]
    before = stock(t, "LIM-LAVANDINA-1L")
    cash_before = D(cj.get(f"{base}/pos/session/current").json()["summary"]["expected_cash"])
    s = sale(cj, base, [{"product_id": t["p"]["LIM-LAVANDINA-1L"]["id"], "quantity": "3"}],
             [{"method": "CASH", "amount": "20000"}]).json()
    assert stock(t, "LIM-LAVANDINA-1L") == before - 3
    v = ger.post(f"{base}/pos/sales/{s['id']}/void", json={"reason": "Cliente se arrepintio"})
    assert v.status_code == 200 and v.json()["status"] == "VOIDED"
    assert stock(t, "LIM-LAVANDINA-1L") == before
    assert D(cj.get(f"{base}/pos/session/current").json()["summary"]["expected_cash"]) == cash_before
    inv = t["c"]["admin"].get(f"{base}/invoices/{s['invoice_id']}").json()
    assert D(inv["balance_due"]) == 0
    assert ger.post(f"{base}/pos/sales/{s['id']}/void", json={"reason": "otra vez"}).json()["status"] == "VOIDED"
    assert gl_balanced(t)


def test_credit_sale_and_collection(t):
    base, cj = t["base"], t["c"]["cajero2"]
    custs = t["c"]["admin"].get(f"{base}/parties", params={"role": "customer", "q": "Supermercado"}).json()["items"]
    cid = custs[0]["id"]
    s = sale(cj, base, [{"product_id": t["p"]["ALM-YERBA-500"]["id"], "quantity": "5"}],
             [{"method": "CASH", "amount": "20000"}], condition="CREDIT", customer_id=cid)
    assert s.status_code == 201 and D(s.json()["paid"]) == 20000 and D(s.json()["change_given"]) == 0
    assert sale(cj, base, [{"product_id": t["p"]["ALM-YERBA-500"]["id"], "quantity": "1"}],
                [{"method": "CASH", "amount": "99999"}], condition="CREDIT", customer_id=cid).status_code == 422
    inv = t["c"]["admin"].get(f"{base}/invoices/{s.json()['invoice_id']}").json()
    assert D(inv["balance_due"]) == 70000 - 20000
    col = cj.post(f"{base}/pos/collections", headers={"Idempotency-Key": uuid.uuid4().hex},
                  json={"customer_id": cid, "amount": "50000"})
    assert col.status_code == 201 and D(col.json()["applied"]) == 50000
    assert gl_balanced(t)


def test_concurrent_last_unit(t):
    """Dos cajas venden la ultima unidad a la vez: una sola venta, stock nunca negativo."""
    base = t["base"]
    admin = t["c"]["admin"]
    pid = t["p"]["LIM-JABON-POLVO-1KG"]["id"]
    have = stock(t, "LIM-JABON-POLVO-1KG")
    if have > 1:
        r = admin.post(f"{base}/stock/adjustments", json={"product_id": pid, "warehouse_id": t["wh"], "direction": "OUT",
                                                          "quantity": str(have - 1), "reason": "Preparar prueba"})
        assert r.status_code == 201, r.text
    assert stock(t, "LIM-JABON-POLVO-1KG") == 1
    sellers = [t["c"]["cajero2"], t["c"]["gerente"]]
    results, barrier = [], threading.Barrier(2)

    def run(c):
        barrier.wait()
        results.append(sale(c, base, [{"product_id": pid, "quantity": "1"}], [{"method": "CASH", "amount": "22000"}]).status_code)

    threads = [threading.Thread(target=run, args=(c,)) for c in sellers]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert sorted(results) == [201, 409] or sorted(results) == [201, 422], results
    assert stock(t, "LIM-JABON-POLVO-1KG") == 0
    assert gl_balanced(t)


def test_dashboard(t):
    data = t["c"]["gerente"].get(f"{t['base']}/pos/dashboard").json()
    assert data["sales_count"] >= 4 and D(data["sales_total"]) > 0 and data["voided_count"] >= 1
    assert data["top_products"] and data["sessions"] and any(s["status"] == "CLOSED" for s in data["sessions"])
    assert all(u["user"] for u in data["by_user"])
    assert t["c"]["cajero1"].get(f"{t['base']}/pos/dashboard").status_code == 403


def test_ean_check_digit():
    assert pos._gtin_ok("7840000000005") and pos._gtin_ok("036000291452") and not pos._gtin_ok("7840000000003")
