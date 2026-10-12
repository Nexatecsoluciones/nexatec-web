"""Sandbox para agentes externos (ChatGPT): una empresa DEMO propia, con los
datos ficticios de siempre, un usuario de servicio (sin contraseña usable)
como Administrador y una llave de API atada SOLO a ese acceso.

Reiniciar el sandbox borra y recrea su base: lo hace una persona por CLI
(nunca un endpoint) y solo funciona sobre empresas creadas por aca
(slug `sandbox-agente-*`): imposible tocar la demo de un cliente."""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api_keys import ApiKey
from app.models.control_plane import Tenant, User
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import (
    Environment, SystemAccessStatus, TenantMemberRole, TenantMemberStatus, TenantStatus,
)
from app.security import api_keys
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database

SLUG_PREFIX = "sandbox-agente-"


class SandboxError(Exception):
    pass


class SandboxExists(SandboxError):
    def __init__(self, access_id: uuid.UUID | None, db_status: str | None):
        super().__init__("Ya existe un sandbox con ese nombre: no se crea otro.")
        self.access_id = access_id
        self.db_status = db_status


def find_sandbox(db: Session, name: str) -> tuple[uuid.UUID | None, str | None] | None:
    """(system_access_id, estado de su base) del sandbox con ese nombre, o None."""
    tenant = db.execute(select(Tenant).where(Tenant.slug.like(f"{SLUG_PREFIX}%"), Tenant.display_name == name)
                        .order_by(Tenant.created_at)).scalars().first()
    if tenant is None:
        return None
    access = db.execute(select(SystemAccess).where(SystemAccess.tenant_id == tenant.id,
                                                   SystemAccess.environment == Environment.DEMO)).scalars().first()
    tdb = db.execute(select(TenantDatabase).where(TenantDatabase.tenant_id == tenant.id)).scalars().first()
    return (access.id if access else None, tdb.status.value if tdb else None)


def create_sandbox(db: Session, name: str = "Sandbox ChatGPT", system_slug: str = "erp",
                   key_days: int = 90) -> tuple[uuid.UUID, str]:
    """Devuelve (system_access_id, llave en claro). Ids como valores simples:
    los objetos ORM se expiran en cada commit y no se leen despues de cerrar
    la sesion (causa del DetachedInstanceError del CLI original)."""
    existing = find_sandbox(db, name)
    if existing is not None:
        raise SandboxExists(*existing)
    system = db.execute(select(System).where(System.slug == system_slug)).scalar_one_or_none()
    if system is None:
        raise SandboxError(f"No existe el sistema '{system_slug}'.")
    sfx = uuid.uuid4().hex[:8]
    tenant = Tenant(slug=f"{SLUG_PREFIX}{sfx}", legal_name=f"{name} (demo ficticia)", display_name=name,
                    status=TenantStatus.ACTIVE)
    db.add(tenant)
    db.flush()
    # Usuario de servicio: la contraseña es aleatoria y nadie la conoce; solo
    # se opera con la llave de API.
    user = User(email=f"agente-{sfx}@agents.nexatecpy.invalid", full_name=f"Agente {name}",
                password_hash=hash_password(secrets.token_urlsafe(48) + "Aa1!"), role=Role.CLIENT_USER,
                tenant_id=tenant.id)
    db.add(user)
    db.flush()
    db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role=TenantMemberRole.CLIENT_ADMIN,
                      status=TenantMemberStatus.ACTIVE))
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.DEMO,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=365))
    db.add(access)
    db.flush()
    access_id, tenant_id, system_id, user_id = access.id, tenant.id, system.id, user.id
    db.commit()
    provision_tenant_database(db, tenant_id, system_id, Environment.DEMO)
    _, raw = api_keys.issue(db, name=f"{name} - llave inicial", user_id=user_id, system_access_id=access_id, days=key_days)
    db.commit()
    return access_id, raw


def _sandbox_access(db: Session, access_id) -> tuple[SystemAccess, Tenant]:
    access = db.get(SystemAccess, access_id)
    tenant = db.get(Tenant, access.tenant_id) if access else None
    if access is None or tenant is None or not tenant.slug.startswith(SLUG_PREFIX) or access.environment != Environment.DEMO:
        raise SandboxError("Ese acceso no es un sandbox de agente: no se toca.")
    return access, tenant


def new_key(db: Session, access_id, name: str, days: int = 90) -> str:
    access, tenant = _sandbox_access(db, access_id)
    user_id = db.execute(select(TenantUser.user_id).where(TenantUser.tenant_id == tenant.id)).scalars().first()
    _, raw = api_keys.issue(db, name=name, user_id=user_id, system_access_id=access.id, days=days)
    db.commit()
    return raw


def revoke_key(db: Session, prefix: str) -> int:
    keys = list(db.execute(select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))).scalars())
    for k in keys:
        k.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return len(keys)


def reset_sandbox(db: Session, access_id) -> None:
    """Borra y recrea la base del sandbox con los datos de demo iniciales.
    Las llaves siguen valiendo (apuntan al acceso, que no cambia)."""
    access, tenant = _sandbox_access(db, access_id)
    tdb = db.execute(select(TenantDatabase).where(TenantDatabase.tenant_id == tenant.id,
                                                  TenantDatabase.system_id == access.system_id,
                                                  TenantDatabase.environment == Environment.DEMO)).scalar_one()
    force_drop_tenant_database_for_tests(tdb.database_identifier)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
    db.commit()
    provision_tenant_database(db, tenant.id, access.system_id, Environment.DEMO)
