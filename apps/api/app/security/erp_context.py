"""Contexto de cada request al ERP: a que base de tenant se conecta y con
que rol actua el usuario. Todo se decide server-side a partir de la sesion
y del system_access_id -- nunca de un tenant_id o URL de base que mande el
navegador.

A diferencia de app/security/tenancy_rbac.py, aca el personal de NEXATEC
(SUPER_ADMIN/ADMIN/SUPPORT/BILLING) NO tiene acceso implicito: los datos
operativos de una empresa (ventas, dinero, terceros) solo los ve quien sea
miembro activo de ese tenant. Si soporte necesita entrar, se lo agrega como
miembro explicitamente (y queda auditado)."""

import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.control_plane import Tenant
from app.models.tenancy import SystemAccess, TenantDatabase, TenantUser
from app.models.tenancy_enums import (
    ProvisioningStatus,
    SystemAccessStatus,
    TenantMemberRole,
    TenantMemberStatus,
    TenantStatus,
)
from app.security.erp_permissions import permissions_for
from app.security.session_auth import CurrentUser, get_current_user
from app.services.tenant_db_manager import TenantDatabaseUnavailable, tenant_db_manager


@dataclass
class ErpContext:
    db: Session
    control_db: Session
    user: CurrentUser
    tenant_id: uuid.UUID
    access: SystemAccess
    member_role: TenantMemberRole

    @property
    def permissions(self) -> frozenset[str]:
        return permissions_for(self.member_role)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def can_write(self) -> bool:
        """Compatibilidad: True si puede escribir en ALGUN modulo."""
        return any(p.endswith((":write", ":deliver", ":receive", ":periods")) for p in self.permissions)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurso no encontrado.")


def get_erp_context(
    system_access_id: uuid.UUID,
    control_db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Generator[ErpContext, None, None]:
    access = control_db.get(SystemAccess, system_access_id)
    if access is None:
        raise _not_found()

    membership = control_db.execute(
        select(TenantUser).where(
            TenantUser.tenant_id == access.tenant_id,
            TenantUser.user_id == user.id,
            TenantUser.status == TenantMemberStatus.ACTIVE,
        )
    ).scalar_one_or_none()
    if membership is None:
        # 404, no 403: no confirmar a un no-miembro que el recurso existe.
        raise _not_found()

    tenant = control_db.get(Tenant, access.tenant_id)
    if tenant is None or tenant.status != TenantStatus.ACTIVE or tenant.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="La empresa no esta activa.")

    if access.status != SystemAccessStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El acceso no esta activo.")
    if access.expires_at is not None and access.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El acceso esta vencido.")

    tenant_db = control_db.execute(
        select(TenantDatabase).where(
            TenantDatabase.tenant_id == access.tenant_id,
            TenantDatabase.system_id == access.system_id,
            TenantDatabase.environment == access.environment,
        )
    ).scalar_one_or_none()
    if tenant_db is None or tenant_db.status != ProvisioningStatus.READY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Entorno no disponible.")

    try:
        engine = tenant_db_manager.get_engine(tenant_db, tenant_db.credential)
    except TenantDatabaseUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Entorno no disponible.")

    session = Session(bind=engine, autoflush=False)
    try:
        yield ErpContext(
            db=session, control_db=control_db, user=user, tenant_id=access.tenant_id,
            access=access, member_role=membership.role,
        )
    finally:
        session.close()


def require(permission: str):
    """Dependencia por endpoint: exige un permiso de la matriz
    (app/security/erp_permissions.py). 403 si el rol no lo tiene."""

    def dep(ctx: ErpContext = Depends(get_erp_context)) -> ErpContext:
        if not ctx.can(permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu rol no tiene permiso para esta accion.")
        return ctx

    return dep
