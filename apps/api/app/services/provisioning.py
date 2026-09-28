"""Servicio de provisioning: la UNICA parte del sistema que puede crear
bases de datos y roles reales de PostgreSQL. Usa su propia conexion con
las credenciales de `nexatec_provisioner` (CREATEDB+CREATEROLE, sin
SUPERUSER) -- la API normal (nexatec_app) nunca podria ejecutar esto
aunque quisiera, porque su rol de base de datos no tiene esos permisos.

No hace DROP DATABASE nunca (el borrado fisico queda para una fase
posterior con retencion y doble confirmacion, tal como se pidio
explicitamente). Nota para esa fase futura: como GRANT/REVOKE de la
membership sobre el rol del tenant es transitorio (ver
_create_role_and_database), un DROP DATABASE real necesitara volver a
otorgarse esa membership (o usar una conexion con el superusuario) antes
de poder borrar -- nexatec_provisioner no queda con esa membership en
reposo a proposito, por minimo privilegio."""

import logging
import secrets
import uuid
from dataclasses import dataclass

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from app.models.tenancy import TenantDatabase, TenantDatabaseCredential
from app.models.tenancy_enums import Environment, ProvisioningStatus
from app.services.db_naming import assert_safe_identifier, build_tenant_database_identifier

logger = logging.getLogger("nexatec.provisioning")

settings = get_settings()

# Motor separado, dedicado solo a DDL de provisioning. autocommit=True
# porque CREATE DATABASE no puede ejecutarse dentro de una transaccion.
_provisioner_engine = create_engine(
    settings.provisioner_maintenance_db_url,
    isolation_level="AUTOCOMMIT",
    pool_pre_ping=True,
)


class ProvisioningError(Exception):
    pass


@dataclass
class ProvisionedDatabase:
    tenant_database: TenantDatabase


def _create_role_and_database(identifier: str) -> str:
    """Ejecuta el DDL real contra PostgreSQL usando psycopg.sql para que
    tanto el identificador como la password queden correctamente escapados
    -- nunca se interpola un string crudo en el SQL."""
    assert_safe_identifier(identifier)
    role_password = secrets.token_urlsafe(32)

    with _provisioner_engine.connect() as conn:
        raw_conn = conn.connection.driver_connection
        with raw_conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "CREATE ROLE {role} LOGIN PASSWORD {password} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(role=sql.Identifier(identifier), password=sql.Literal(role_password))
            )

    try:
        with _provisioner_engine.connect() as conn:
            raw_conn = conn.connection.driver_connection
            with raw_conn.cursor() as cur:
                # PostgreSQL 16: un rol con CREATEROLE ya no obtiene
                # membership automatica sobre los roles que crea. Se
                # necesita esa membership para poder asignarlo como OWNER
                # de la base nueva (SET ROLE implicito); se otorga y se
                # revoca en la misma operacion para no dejar a
                # nexatec_provisioner con acceso permanente a cada base de
                # tenant (minimo privilegio tambien despues de usarlo).
                cur.execute(
                    sql.SQL("GRANT {role} TO {grantee}").format(
                        role=sql.Identifier(identifier),
                        grantee=sql.Identifier(settings.provisioner_db_user),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE DATABASE {db} OWNER {role}").format(
                        db=sql.Identifier(identifier), role=sql.Identifier(identifier)
                    )
                )
                cur.execute(
                    sql.SQL("REVOKE ALL ON DATABASE {db} FROM PUBLIC").format(
                        db=sql.Identifier(identifier)
                    )
                )
                cur.execute(
                    sql.SQL("REVOKE {role} FROM {grantee}").format(
                        role=sql.Identifier(identifier),
                        grantee=sql.Identifier(settings.provisioner_db_user),
                    )
                )
    except Exception:
        # El rol quedo creado pero la base no: no dejar un rol huerfano sin
        # base asociada.
        _drop_role_best_effort(identifier)
        raise

    return role_password


def _drop_role_best_effort(identifier: str) -> None:
    """Limpieza si CREATE DATABASE falla despues de crear el rol. Nunca se
    propaga un error de limpieza por encima del error original."""
    try:
        assert_safe_identifier(identifier)
        with _provisioner_engine.connect() as conn:
            raw_conn = conn.connection.driver_connection
            with raw_conn.cursor() as cur:
                cur.execute(sql.SQL("DROP ROLE IF EXISTS {role}").format(role=sql.Identifier(identifier)))
    except Exception:
        logger.exception("cleanup_failed_drop_role identifier=%s", identifier)


def force_drop_tenant_database_for_tests(identifier: str) -> None:
    """SOLO para limpieza de tests automatizados. La aplicacion real nunca
    llama a esto (ver nota de DROP DATABASE arriba); vuelve a otorgarse
    membership transitoriamente -- igual que haria un futuro flujo real de
    deprovisioning -- y borra."""
    assert_safe_identifier(identifier)
    with _provisioner_engine.connect() as conn:
        raw_conn = conn.connection.driver_connection
        with raw_conn.cursor() as cur:
            try:
                cur.execute(
                    sql.SQL("GRANT {role} TO {grantee}").format(
                        role=sql.Identifier(identifier), grantee=sql.Identifier(settings.provisioner_db_user)
                    )
                )
            except Exception:
                # El rol puede no existir (limpieza repetida / estado ya
                # parcialmente inconsistente en un test anterior). No es
                # motivo para no intentar el DROP igual.
                conn.rollback()
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {db}").format(db=sql.Identifier(identifier)))
            cur.execute(sql.SQL("DROP ROLE IF EXISTS {role}").format(role=sql.Identifier(identifier)))


def provision_tenant_database(
    control_db: Session,
    tenant_id: uuid.UUID,
    system_id: uuid.UUID,
    environment: Environment,
) -> TenantDatabase:
    """Crea (o reutiliza si ya existe y esta READY) la base fisica para
    tenant+sistema+entorno. Lanza ProvisioningError sin dejar rastros de
    secretos en el mensaje si algo falla."""

    existing = (
        control_db.query(TenantDatabase)
        .filter(
            TenantDatabase.tenant_id == tenant_id,
            TenantDatabase.system_id == system_id,
            TenantDatabase.environment == environment,
        )
        .one_or_none()
    )
    if existing is not None and existing.status == ProvisioningStatus.READY:
        return existing

    identifier = build_tenant_database_identifier(tenant_id, system_id, environment)

    tenant_db = existing or TenantDatabase(
        tenant_id=tenant_id,
        system_id=system_id,
        environment=environment,
        database_identifier=identifier,
        database_name=identifier,
        database_user_ref=identifier,
    )
    tenant_db.status = ProvisioningStatus.PROVISIONING
    control_db.add(tenant_db)
    control_db.commit()
    control_db.refresh(tenant_db)

    try:
        role_password = _create_role_and_database(identifier)
    except Exception as exc:  # noqa: BLE001 - se traduce a estado, nunca se expone crudo
        logger.exception("provisioning_failed tenant_database_id=%s", tenant_db.id)
        tenant_db.status = ProvisioningStatus.FAILED
        tenant_db.last_error = f"{type(exc).__name__} al crear rol/base."
        control_db.commit()
        raise ProvisioningError("No se pudo aprovisionar la base de datos.") from exc

    try:
        credential = TenantDatabaseCredential(
            tenant_database_id=tenant_db.id,
            encrypted_password=encrypt_secret(role_password),
        )
        control_db.add(credential)
        tenant_db.status = ProvisioningStatus.READY
        tenant_db.schema_version = "0"
        tenant_db.last_error = None
        control_db.commit()
    except Exception as exc:  # noqa: BLE001
        # La DB/rol ya se crearon en PostgreSQL pero no pudimos registrar la
        # credencial cifrada: no se puede dejar el registro en READY sin
        # password utilizable, y tampoco intentamos DROP DATABASE aca (regla
        # explicita de no borrar automaticamente). Queda en FAILED para que
        # un admin lo revise; la base fisica huerfana se limpia manualmente.
        logger.exception("credential_persist_failed tenant_database_id=%s", tenant_db.id)
        tenant_db.status = ProvisioningStatus.FAILED
        tenant_db.last_error = f"{type(exc).__name__} al registrar credencial."
        control_db.commit()
        raise ProvisioningError("No se pudo registrar la credencial de la base.") from exc

    return tenant_db
