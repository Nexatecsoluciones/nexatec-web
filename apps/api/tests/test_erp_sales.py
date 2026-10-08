"""Pedidos de venta sobre una base de tenant REAL: IVA incluido, reserva y
entrega de stock, transiciones, numeracion sin huecos y valores congelados."""

import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services import inventory, sales
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database
from app.services.tenant_db_manager import tenant_db_manager

PASSWORD = "ClaveDePruebaSegura123"
D = Decimal


@pytest.mark.parametrize("qty,price,disc,rate,expected", [
    (D("3"), D("5000"), D("0"), D("10"), (D("13636"), D("1364"), D("15000"))),
    (D("1"), D("10000"), D("0"), D("5"), (D("9524"), D("476"), D("10000"))),
    (D("2"), D("7000"), D("0"), D("0"), (D("14000"), D("0"), D("14000"))),
    (D("3"), D("5000"), D("10"), D("10"), (D("12273"), D("1227"), D("13500"))),
])
def test_compute_line_iva_included_pyg(qty, price, disc, rate, expected):
    assert sales.compute_line(qty, price, disc, rate, 0) == expected


@pytest.fixture(scope="module")
def s():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-sales-{sfx}", name="Sales", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"sales-{sfx}", legal_name="Sales SRL", display_name="Sales", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    admin = User(email=f"sales-admin-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
    viewer = User(email=f"sales-viewer-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
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

    def login(email):
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
        return c

    c = login(admin.email)
    base = f"/api/erp/{access.id}"
    branch = c.post(f"{base}/branches", json={"code": "B1", "name": "Central"}).json()
    wh = c.post(f"{base}/warehouses", json={"code": "W1", "name": "Uno", "branch_id": branch["id"]}).json()["id"]
    units = {u["code"]: u["id"] for u in c.get(f"{base}/units").json()}
    customer = c.post(f"{base}/parties", json={"legal_name": "Cliente SA", "is_customer": True}).json()["id"]
    supplier = c.post(f"{base}/parties", json={"legal_name": "Proveedor SA", "is_supplier": True}).json()["id"]

    def product(sku, price, ptype="GOOD", tax="IVA10", stock=None, cost="1000"):
        body = {"sku": sku, "name": sku, "product_type": ptype, "unit_id": units["UN" if ptype == "GOOD" else "SRV"],
                "tax_code": tax, "sale_price": price}
        pid = c.post(f"{base}/products", json=body).json()["id"]
        if stock:
            r = c.post(f"{base}/stock/receipts", json={"product_id": pid, "warehouse_id": wh, "quantity": stock, "unit_cost": cost})
            assert r.status_code == 201
        return pid

    yield {"c": c, "viewer": login(viewer.email), "base": base, "wh": wh, "customer": customer,
           "supplier": supplier, "product": product, "engine": tenant_db_manager.get_engine(tdb, tdb.credential),
           "admin_id": admin.id}

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


def _bal(s, pid):
    items = s["c"].get(f"{s['base']}/stock/balances", params={"product_id": pid}).json()["items"]
    return (D(items[0]["on_hand"]), D(items[0]["reserved"])) if items else (D(0), D(0))


def _order(s, lines, cond="CASH"):
    return s["c"].post(f"{s['base']}/sales/orders", json={
        "customer_id": s["customer"], "warehouse_id": s["wh"], "payment_condition": cond, "lines": lines})


def test_full_order_flow(s):
    c, base = s["c"], s["base"]
    good = s["product"]("GOOD-1", "5000", stock="10", cost="3000")
    srv = s["product"]("SRV-1", "100000", ptype="SERVICE")

    r = _order(s, [{"product_id": good, "quantity": "3"}, {"product_id": srv, "quantity": "1"}])
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["number"].startswith("OV-") and o["status"] == "DRAFT"
    assert (D(o["total"]), D(o["tax_total"]), D(o["subtotal_net"])) == (D("115000"), D("10455"), D("104545"))

    conf = c.post(f"{base}/sales/orders/{o['id']}/confirm")
    assert conf.status_code == 200 and conf.json()["status"] == "CONFIRMED"
    assert _bal(s, good) == (D("10"), D("3"))

    dlv = c.post(f"{base}/sales/orders/{o['id']}/deliver")
    assert dlv.status_code == 200 and dlv.json()["status"] == "DELIVERED"
    assert _bal(s, good) == (D("7"), D("0"))
    lines = {ln["product_id"]: ln for ln in dlv.json()["lines"]}
    assert D(lines[good]["unit_cost"]) == D("3000") and lines[srv]["unit_cost"] is None

    again = c.post(f"{base}/sales/orders/{o['id']}/deliver")
    assert again.status_code == 200 and _bal(s, good) == (D("7"), D("0"))
    assert c.post(f"{base}/sales/orders/{o['id']}/cancel", json={"reason": "ya no lo quiere"}).status_code == 409
    assert c.put(f"{base}/sales/orders/{o['id']}/lines", json={"lines": [{"product_id": good, "quantity": "1"}]}).status_code == 409

    kardex = c.get(f"{base}/stock/movements", params={"product_id": good}).json()["items"]
    assert kardex[0]["movement_type"] == "ISSUE" and kardex[0]["reference"] == o["number"]


def test_reservation_blocks_overselling_and_cancel_releases(s):
    c, base = s["c"], s["base"]
    pid = s["product"]("RES-1", "1000", stock="5")
    a = _order(s, [{"product_id": pid, "quantity": "4"}]).json()
    b = _order(s, [{"product_id": pid, "quantity": "2"}]).json()
    assert c.post(f"{base}/sales/orders/{a['id']}/confirm").status_code == 200
    blocked = c.post(f"{base}/sales/orders/{b['id']}/confirm")
    assert blocked.status_code == 409
    assert c.get(f"{base}/sales/orders/{b['id']}").json()["status"] == "DRAFT"

    cancel = c.post(f"{base}/sales/orders/{a['id']}/cancel", json={"reason": "cliente desistio"})
    assert cancel.status_code == 200 and cancel.json()["cancel_reason"] == "cliente desistio"
    assert _bal(s, pid) == (D("5"), D("0"))
    assert c.post(f"{base}/sales/orders/{b['id']}/confirm").status_code == 200


def test_draft_edit_and_frozen_prices(s):
    c, base = s["c"], s["base"]
    pid = s["product"]("FRZ-1", "2000", stock="10")
    o = _order(s, [{"product_id": pid, "quantity": "1"}]).json()
    edited = c.put(f"{base}/sales/orders/{o['id']}/lines", json={"lines": [{"product_id": pid, "quantity": "2", "discount_pct": "50"}]})
    assert edited.status_code == 200 and D(edited.json()["total"]) == D("2000")
    c.patch(f"{base}/products/{pid}", json={"sale_price": "9999"})
    assert D(c.get(f"{base}/sales/orders/{o['id']}").json()["lines"][0]["unit_price"]) == D("2000")


def test_validation_and_gapless_numbering(s):
    c, base = s["c"], s["base"]
    pid = s["product"]("NUM-1", "1000", stock="1")
    first = _order(s, [{"product_id": pid, "quantity": "1"}]).json()["number"]
    bad = c.post(f"{base}/sales/orders", json={"customer_id": s["supplier"], "warehouse_id": s["wh"],
                                               "payment_condition": "CASH", "lines": [{"product_id": pid, "quantity": "1"}]})
    assert bad.status_code == 422
    assert _order(s, [{"product_id": str(uuid.uuid4()), "quantity": "1"}]).status_code == 422
    assert _order(s, []).status_code == 422
    nxt = _order(s, [{"product_id": pid, "quantity": "1"}]).json()["number"]
    assert int(nxt[3:]) == int(first[3:]) + 1


def test_viewer_cannot_sell_and_list_filters(s):
    pid = s["product"]("VW-1", "1000")
    r = s["viewer"].post(f"{s['base']}/sales/orders", json={"customer_id": s["customer"], "warehouse_id": s["wh"],
                                                          "payment_condition": "CASH", "lines": [{"product_id": pid, "quantity": "1"}]})
    assert r.status_code == 403
    page = s["viewer"].get(f"{s['base']}/sales/orders", params={"status": "DELIVERED"}).json()
    assert page["total"] >= 1 and all(o["status"] == "DELIVERED" for o in page["items"])


def test_concurrent_confirmations_never_overreserve(s):
    pid = s["product"]("CC-1", "1000", stock="10")
    ids = [_order(s, [{"product_id": pid, "quantity": "3"}]).json()["id"] for _ in range(6)]
    results, barrier = [], threading.Barrier(len(ids))

    def worker(oid):
        barrier.wait()
        with Session(bind=s["engine"]) as db:
            try:
                sales.confirm(db, uuid.UUID(oid), s["admin_id"])
                db.commit()
                results.append("ok")
            except inventory.InsufficientStock:
                db.rollback()
                results.append("no")

    threads = [threading.Thread(target=worker, args=(i,)) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 3 and results.count("no") == 3
    assert _bal(s, pid) == (D("10"), D("9"))


def test_delivery_movement_cannot_be_reversed_directly(s):
    c, base = s["c"], s["base"]
    pid = s["product"]("NOREV-1", "1000", stock="5")
    o = _order(s, [{"product_id": pid, "quantity": "2"}]).json()
    c.post(f"{base}/sales/orders/{o['id']}/confirm")
    c.post(f"{base}/sales/orders/{o['id']}/deliver")
    issue = next(m for m in c.get(f"{base}/stock/movements", params={"product_id": pid}).json()["items"]
                 if m["movement_type"] == "ISSUE")
    r = c.post(f"{base}/stock/movements/{issue['id']}/reverse", json={"reason": "intento"})
    assert r.status_code == 422 and "documento" in r.json()["detail"]
    assert _bal(s, pid) == (D("3"), D("0"))
