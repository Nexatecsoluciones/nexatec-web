"""Empresa demo ficticia: se carga sola en bases DEMO, nunca en
PRODUCTION, todo marcado como ficticio, e idempotente."""

import uuid

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal
from app.models.control_plane import Tenant
from app.models.system import System
from app.models.tenancy import TenantDatabase, TenantDatabaseCredential
from app.models.tenancy_enums import Environment, TenantStatus
from app.services.demo_seed import DEMO_COMPANY_NAME, seed_demo_company
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database
from app.services.ruc import is_valid
from app.services.tenant_db_manager import tenant_db_manager


@pytest.fixture(scope="module")
def dbs():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-seed-{sfx}", name="Seed", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"seed-{sfx}", legal_name="Seed SRL", display_name="Seed", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    demo = provision_tenant_database(db, tenant.id, system.id, Environment.DEMO)
    prod = provision_tenant_database(db, tenant.id, system.id, Environment.PRODUCTION)
    yield {
        "demo": tenant_db_manager.get_engine(demo, demo.credential),
        "prod": tenant_db_manager.get_engine(prod, prod.credential),
    }
    for d in (demo, prod):
        force_drop_tenant_database_for_tests(d.database_identifier)
        db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == d.id).delete()
        db.query(TenantDatabase).filter(TenantDatabase.id == d.id).delete()
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def test_demo_database_has_fictitious_company(dbs):
    with dbs["demo"].connect() as c:
        name, fict, ruc, dv = c.execute(text("SELECT legal_name, ruc_is_fictitious, ruc, ruc_dv FROM company")).one()
    assert name == DEMO_COMPANY_NAME and "SIMULACI" in name
    assert fict is True
    assert ruc.startswith("999") and is_valid(ruc, dv)


def test_demo_catalog_and_parties(dbs):
    with dbs["demo"].connect() as c:
        products = c.execute(text("SELECT count(*), min(sale_price) FROM products")).one()
        statuses = {r[0] for r in c.execute(text("SELECT DISTINCT ruc_status FROM parties"))}
        rucs = [r[0] for r in c.execute(text("SELECT ruc FROM parties"))]
        customers = c.execute(text("SELECT count(*) FROM parties WHERE is_customer")).scalar()
        suppliers = c.execute(text("SELECT count(*) FROM parties WHERE is_supplier")).scalar()
        services_with_stock = c.execute(text(
            "SELECT count(*) FROM products WHERE product_type='SERVICE' AND tracks_stock")).scalar()
        warehouses = c.execute(text("SELECT count(*) FROM warehouses")).scalar()
    assert products[0] >= 10 and products[1] > 0
    assert statuses == {"FICTITIOUS"}
    assert all(r.startswith("999") for r in rucs)
    assert customers >= 5 and suppliers >= 3
    assert services_with_stock == 0
    assert warehouses == 2


def test_production_database_starts_empty(dbs):
    with dbs["prod"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM company")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM products")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM parties")).scalar() == 0


def test_seed_is_idempotent(dbs):
    assert seed_demo_company(dbs["demo"]) is False
    with dbs["demo"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM company")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM branches")).scalar() == 1


def test_demo_has_consistent_operations(dbs):
    with dbs["demo"].connect() as c:
        q = lambda sql: c.execute(text(sql)).scalar()  # noqa: E731
        assert q("SELECT count(*) FROM sales_invoices WHERE status='ISSUED'") == 5
        assert q("SELECT count(*) FROM crm_leads WHERE status='OPEN'") == 3
        assert q("SELECT count(*) FROM crm_opportunities WHERE stage IN ('WON','LOST')") == 2
        assert q("SELECT count(*) FROM crm_opportunities WHERE stage NOT IN ('WON','LOST')") == 4
        assert q("SELECT count(*) FROM crm_activities WHERE done_at IS NULL") == 4
        assert q("SELECT count(*) FROM sales_invoices WHERE fiscal_status <> 'INTERNAL_SIMULATION'") == 0
        assert q("SELECT count(*) FROM sales_orders WHERE status='CONFIRMED'") == 1
        assert q("SELECT count(*) FROM sales_orders WHERE status='DRAFT'") == 1
        assert q("SELECT coalesce(sum(balance_due),0) FROM sales_invoices") > 0          # hay deuda de clientes
        assert q("SELECT coalesce(sum(balance_due),0) FROM supplier_invoices") > 0       # y a proveedores
        assert q("SELECT coalesce(sum(reserved),0) FROM stock_balances") > 0             # pedido confirmado reserva
        assert q("SELECT count(*) FROM purchase_orders WHERE status='PARTIALLY_RECEIVED'") == 1
        assert q("SELECT min(on_hand) FROM stock_balances") >= 0
        # Toda la contabilidad cuadra, asiento por asiento y en total.
        assert q("SELECT count(*) FROM (SELECT entry_id FROM journal_lines GROUP BY entry_id "
                 "HAVING sum(debit) <> sum(credit)) x") == 0
        assert q("SELECT sum(debit) - sum(credit) FROM journal_lines") == 0
        # Caja no queda negativa: el aporte de capital cubre los pagos.
        assert q("SELECT sum(l.debit - l.credit) FROM journal_lines l JOIN accounts a ON a.id = l.account_id "
                 "WHERE a.code = '1.1.01'") > 0
        assert q("SELECT sum(l.debit - l.credit) FROM journal_lines l JOIN accounts a ON a.id = l.account_id "
                 "WHERE a.code = '1.1.02'") > 0
