"""Aplica las migraciones de tenant_alembic/ a una base de tenant.

Se conecta con el ROL PROPIO del tenant (dueno de su base), via
tenant_db_manager -- nunca con nexatec_app ni nexatec_provisioner. Asi la
migracion no puede, ni por error, tocar otra base que no sea la suya.

Seguro de re-ejecutar: Alembic solo aplica las revisiones que faltan, y
cada upgrade corre en una transaccion (DDL transaccional de PostgreSQL),
asi que una falla a mitad de camino no deja la base a medio migrar."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session

from app.models.tenancy import TenantDatabase
from app.models.tenancy_enums import ProvisioningStatus
from app.services.tenant_db_manager import tenant_db_manager

logger = logging.getLogger("nexatec.tenant_migrations")

_API_ROOT = Path(__file__).resolve().parents[2]


class TenantMigrationError(Exception):
    pass


def _alembic_config() -> Config:
    cfg = Config(str(_API_ROOT / "tenant_alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "tenant_alembic"))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def current_revision(tenant_db: TenantDatabase) -> str | None:
    engine = tenant_db_manager.get_engine(tenant_db, tenant_db.credential)
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def migrate_tenant_database(control_db: Session, tenant_db: TenantDatabase) -> str:
    """Lleva la base del tenant a head y registra la revision en
    tenant_databases.schema_version. Lanza TenantMigrationError (sin
    detalles internos en el mensaje) si falla; el detalle queda en logs."""
    if tenant_db.status != ProvisioningStatus.READY or tenant_db.credential is None:
        raise TenantMigrationError("La base del tenant no esta lista para migrar.")

    cfg = _alembic_config()
    try:
        engine = tenant_db_manager.get_engine(tenant_db, tenant_db.credential)
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            revision = MigrationContext.configure(conn).get_current_revision()
    except Exception as exc:  # noqa: BLE001 - se traduce, nunca se expone crudo
        logger.exception("tenant_migration_failed tenant_database_id=%s", tenant_db.id)
        tenant_db.last_error = f"{type(exc).__name__} al migrar esquema."
        control_db.commit()
        raise TenantMigrationError("No se pudo migrar el esquema de la base del tenant.") from exc

    tenant_db.schema_version = revision
    tenant_db.last_error = None
    control_db.commit()
    logger.info("tenant_migrated tenant_database_id=%s revision=%s", tenant_db.id, revision)
    return revision
