import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.db import get_db
from app.core.net import safe_ip
from app.models.system import System
from app.models.tenancy import SystemAccess
from app.models.tenancy_enums import Environment, SystemAccessStatus
from app.security.rbac import require_admin_panel

router = APIRouter(prefix="/api/admin/system-access", tags=["admin", "system-access"])


class SystemAccessCreate(BaseModel):
    tenant_id: uuid.UUID
    system_id: uuid.UUID
    environment: Environment
    max_users: int | None = None
    storage_limit_mb: int | None = None
    request_limit: int | None = None


class SystemAccessUpdate(BaseModel):
    status: SystemAccessStatus | None = None
    expires_at: datetime | None = None
    max_users: int | None = None
    storage_limit_mb: int | None = None
    request_limit: int | None = None


class SystemAccessOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    system_id: uuid.UUID
    environment: Environment
    status: SystemAccessStatus
    starts_at: datetime | None
    expires_at: datetime | None
    max_users: int | None
    storage_limit_mb: int | None
    request_limit: int | None

    model_config = ConfigDict(from_attributes=True)


def _get_or_404(db: Session, system_access_id: uuid.UUID) -> SystemAccess:
    row = db.get(SystemAccess, system_access_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entitlement no encontrado.")
    return row


@router.post("", response_model=SystemAccessOut, status_code=status.HTTP_201_CREATED)
def create_system_access(
    payload: SystemAccessCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    system = db.get(System, payload.system_id)
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sistema no encontrado.")

    existing = db.execute(
        select(SystemAccess).where(
            SystemAccess.tenant_id == payload.tenant_id,
            SystemAccess.system_id == payload.system_id,
            SystemAccess.environment == payload.environment,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un entitlement para ese tenant+sistema+entorno.",
        )

    access = SystemAccess(
        tenant_id=payload.tenant_id,
        system_id=payload.system_id,
        environment=payload.environment,
        status=SystemAccessStatus.PENDING,
        max_users=payload.max_users,
        storage_limit_mb=payload.storage_limit_mb,
        request_limit=payload.request_limit,
    )
    db.add(access)
    db.flush()

    log_audit(
        db,
        actor_user_id=admin.id,
        tenant_id=payload.tenant_id,
        action="SYSTEM_ACCESS_CREATED",
        resource=f"system_access:{access.id}",
        ip_address=safe_ip(request.client.host if request.client else None),
        metadata={"system_id": str(payload.system_id), "environment": payload.environment.value},
    )
    db.commit()
    db.refresh(access)
    return access


@router.get("", response_model=list[SystemAccessOut])
def list_system_access(
    tenant_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    stmt = select(SystemAccess)
    if tenant_id is not None:
        stmt = stmt.where(SystemAccess.tenant_id == tenant_id)
    stmt = stmt.order_by(SystemAccess.created_at.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.patch("/{system_access_id}", response_model=SystemAccessOut)
def update_system_access(
    system_access_id: uuid.UUID,
    payload: SystemAccessUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    access = _get_or_404(db, system_access_id)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(access, field, value)

    action = "SYSTEM_ACCESS_SUSPENDED" if updates.get("status") == SystemAccessStatus.SUSPENDED else "SYSTEM_ACCESS_UPDATED"
    log_audit(
        db,
        actor_user_id=admin.id,
        tenant_id=access.tenant_id,
        action=action,
        resource=f"system_access:{access.id}",
        ip_address=safe_ip(request.client.host if request.client else None),
        metadata={"fields": list(updates.keys())},
    )
    db.commit()
    db.refresh(access)
    return access
