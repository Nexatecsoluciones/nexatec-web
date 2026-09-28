import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.system import System
from app.models.tenancy import SystemAccess
from app.models.tenancy_enums import SystemAccessStatus
from app.security.session_auth import CurrentUser, get_current_user
from app.security.tenancy_rbac import assert_tenant_membership, get_active_tenant_ids

router = APIRouter(prefix="/api/portal", tags=["portal"])

# Estados que efectivamente se muestran en el portal. PENDING (todavia sin
# aprovisionar) y REVOKED nunca aparecen -- "no contratado" no es un
# entitlement visible, es la ausencia de uno.
_VISIBLE_STATUSES = (
    SystemAccessStatus.ACTIVE,
    SystemAccessStatus.SUSPENDED,
    SystemAccessStatus.EXPIRED,
)


class MySystemOut(BaseModel):
    system_access_id: uuid.UUID
    system_slug: str
    system_name: str
    environment: str
    status: str
    expires_at: datetime | None


@router.get("/my-systems", response_model=list[MySystemOut])
def my_systems(
    db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)
):
    tenant_ids = get_active_tenant_ids(db, current_user)
    if not tenant_ids:
        return []

    rows = db.execute(
        select(SystemAccess, System)
        .join(System, System.id == SystemAccess.system_id)
        .where(
            SystemAccess.tenant_id.in_(tenant_ids),
            SystemAccess.status.in_(_VISIBLE_STATUSES),
        )
        .order_by(SystemAccess.created_at.desc())
    ).all()

    return [
        MySystemOut(
            system_access_id=access.id,
            system_slug=system.slug,
            system_name=system.name,
            environment=access.environment.value,
            status=access.status.value,
            expires_at=access.expires_at,
        )
        for access, system in rows
    ]


@router.post("/my-systems/{system_access_id}/access")
def request_access(
    system_access_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Backend del boton "Acceder": el frontend nunca construye una URL
    interna, solo pide el destino y la API decide si corresponde. Sin
    gateway todavia (FASE 7), devuelve un estado honesto en vez de una URL
    o de datos de infraestructura interna."""
    access = db.get(SystemAccess, system_access_id)
    if access is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")

    # Verifica membership SIEMPRE server-side -- nunca se confia en que el
    # id venga "bien" solo porque el usuario esta autenticado.
    assert_tenant_membership(db, current_user, access.tenant_id)

    if access.status != SystemAccessStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El acceso no esta activo.")

    if access.expires_at is not None and access.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El acceso esta vencido.")

    return {"status": "not_published_yet", "message": "Entorno preparado. Publicacion pendiente."}
