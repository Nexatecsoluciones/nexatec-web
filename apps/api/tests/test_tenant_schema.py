"""Esquema del ERP dentro de una base de tenant REAL (provisioning real,
sin mocks): queda en head al aprovisionar, la migracion es idempotente,
trae los datos de referencia, y las reglas de integridad viven en la base
(CHECK/UNIQUE), no solo en la aplicacion."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.models.control_plane import Tenant
from app.models.system import System
from app.models.tenancy import TenantDatabase, TenantDatabaseCredential
from app.models.tenancy_enums import Environment, TenantStatus
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database
from app.services.tenant_db_manager import tenant_db_manager
from app.services.tenant_migrations import head_revision, migrate_tenant_database


@pytest.fixture()
def provisioned():
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-schema-{suffix}", name="ERP Schema Test", short_description="x",
                    category="Test", demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"schema-{suffix}", legal_name="Schema SRL", display_name="Schema", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()

    tenant_db = provision_tenant_database(db, tenant.id, system.id, Environment.DEMO)
    engine = tenant_db_manager.get_engine(tenant_db, tenant_db.credential)
    yield {"db": db, "tenant_db": tenant_db, "engine": engine}

    force_drop_tenant_database_for_tests(tenant_db.database_identifier)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tenant_db.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tenant_db.id).delete()
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def test_provisioned_database_is_at_head_with_reference_data(provisioned):
    tenant_db = provisioned["tenant_db"]
    assert tenant_db.schema_version == head_revision()

    with provisioned["engine"].connect() as conn:
        currencies = dict(conn.execute(text("SELECT code, decimals FROM currencies")).all())
        taxes = {code: rate for code, rate in conn.execute(text("SELECT code, rate FROM taxes")).all()}
        units = {r[0] for r in conn.execute(text("SELECT code FROM units_of_measure")).all()}
    assert currencies == {"PYG": 0, "USD": 2}
    assert taxes == {"IVA10": Decimal("10.00"), "IVA5": Decimal("5.00"), "EXENTA": Decimal("0.00")}
    assert {"UN", "KG", "HR", "SRV"} <= units


def test_migration_is_idempotent(provisioned):
    db, tenant_db = provisioned["db"], provisioned["tenant_db"]
    first = tenant_db.schema_version
    assert migrate_tenant_database(db, tenant_db) == first
    with provisioned["engine"].connect() as conn:
        # Los datos de referencia no se duplicaron al re-migrar.
        assert conn.execute(text("SELECT count(*) FROM currencies")).scalar() == 2


def test_company_table_allows_single_row_only(provisioned):
    engine = provisioned["engine"]
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO company (id, legal_name, ruc_is_fictitious, base_currency, timezone) "
                          "VALUES (1, 'Empresa', false, 'PYG', 'America/Asuncion')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO company (id, legal_name, ruc_is_fictitious, base_currency, timezone) "
                              "VALUES (2, 'Otra', false, 'PYG', 'America/Asuncion')"))


def test_negative_price_and_party_without_role_rejected_by_database(provisioned):
    engine = provisioned["engine"]
    with engine.connect() as conn:
        unit_id = conn.execute(text("SELECT id FROM units_of_measure WHERE code='UN'")).scalar()

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO products (id, sku, name, product_type, unit_id, tax_code, sale_price, average_cost, "
                "tracks_stock, is_active) VALUES (gen_random_uuid(), 'X1', 'X', 'GOOD', :u, 'IVA10', -1, 0, true, true)"
            ), {"u": unit_id})

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO parties (id, legal_name, ruc_status, is_customer, is_supplier, payment_terms_days, "
                "credit_limit, is_active) VALUES (gen_random_uuid(), 'Nadie', 'UNVERIFIED', false, false, 0, 0, true)"
            ))


def test_tenant_role_cannot_reach_control_plane(provisioned):
    """El rol del tenant (el mismo con el que corre la migracion) no puede
    conectarse a nexatec_control."""
    tenant_db = provisioned["tenant_db"]
    url = provisioned["engine"].url.set(database="nexatec_control")
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError

    other = create_engine(url)
    with pytest.raises(OperationalError):
        with other.connect():
            pass
    other.dispose()
    assert tenant_db.database_user_ref not in ("nexatec_app", "nexatec_provisioner")
