import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenancy import TenantUser
from app.models.tenancy_enums import TenantMemberStatus
from app.security.rbac import ADMIN_ROLES
from app.security.session_auth import CurrentUser


def _not_found() -> HTTPException:
    # 404 en vez de 403 a proposito: no confirmar ante un no-miembro que el
    # recurso/tenant existe (ver seccion IDOR del plan de FASE 4).
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurso no encontrado.")


def assert_tenant_membership(
    db: Session, user: CurrentUser, tenant_id: uuid.UUID
) -> TenantUser | None:
    """Verifica SIEMPRE server-side que el usuario pertenece al tenant que
    se esta consultando/modificando -- nunca se confia en un tenant_id que
    venga del frontend sin este chequeo. El personal de NEXATEC (roles
    admin globales) no necesita membership. Devuelve la fila de membership
    (o None si es admin global) para que el caller pueda inspeccionar el
    rol dentro del tenant (CLIENT_ADMIN vs CLIENT_USER) si lo necesita."""
    if user.role in ADMIN_ROLES:
        return None

    membership = db.execute(
        select(TenantUser).where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.user_id == user.id,
            TenantUser.status == TenantMemberStatus.ACTIVE,
        )
    ).scalar_one_or_none()

    if membership is None:
        raise _not_found()

    return membership


def get_active_tenant_ids(db: Session, user: CurrentUser) -> list[uuid.UUID]:
    """Tenants a los que pertenece el usuario actual (vacio para personal
    de NEXATEC, que no necesita esta lista: ve todo via ADMIN_ROLES)."""
    rows = db.execute(
        select(TenantUser.tenant_id).where(
            TenantUser.user_id == user.id, TenantUser.status == TenantMemberStatus.ACTIVE
        )
    ).scalars()
    return list(rows)
