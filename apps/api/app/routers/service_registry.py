import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.db import get_db
from app.models.control_center import ServiceRegistry
from app.models.control_center_enums import ServiceHealthStatus, ServiceType
from app.models.tenancy_enums import Environment
from app.security.rbac import require_roles
from app.security.roles import Role
from app.services.service_targets import ServiceTargetError, validate_internal_target

router = APIRouter(prefix="/api/admin/service-registry", tags=["admin", "service-registry"])

# Solo SUPER_ADMIN -- este registro decide a que destinos internos puede
# llegar el gateway/health center, es la superficie mas sensible del
# panel admin (ver app/services/service_targets.py, anti-SSRF).
require_super_admin = require_roles(Role.SUPER_ADMIN)


class ServiceRegistryOut(BaseModel):
    id: uuid.UUID
    name: str
    type: ServiceType
    environment: Environment | None
    public_hostname: str | None
    healthcheck_path: str
    version: str | None
    status: ServiceHealthStatus
    last_checked_at: datetime | None
    created_at: datetime
    # NUNCA se incluye internal_target aca -- ver ServiceRegistryDetailOut,
    # que existe separado y sigue exigiendo SUPER_ADMIN igual.

    model_config = ConfigDict(from_attributes=True)


class ServiceRegistryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    type: ServiceType
    environment: Environment | None = None
    public_hostname: str | None = Field(default=None, max_length=255)
    internal_target: str = Field(min_length=3, max_length=255)
    healthcheck_path: str = Field(default="/", max_length=255)
    version: str | None = Field(default=None, max_length=40)


@router.get("", response_model=list[ServiceRegistryOut], dependencies=[Depends(require_super_admin)])
def list_services(db: Session = Depends(get_db)):
    return db.execute(select(ServiceRegistry).order_by(ServiceRegistry.name)).scalars().all()


@router.post("", response_model=ServiceRegistryOut, status_code=status.HTTP_201_CREATED)
def create_service(payload: ServiceRegistryCreate, db: Session = Depends(get_db), admin=Depends(require_super_admin)):
    try:
        validate_internal_target(payload.type, payload.internal_target)
    except ServiceTargetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    service = ServiceRegistry(
        name=payload.name, type=payload.type, environment=payload.environment,
        public_hostname=payload.public_hostname, internal_target=payload.internal_target,
        healthcheck_path=payload.healthcheck_path, version=payload.version,
    )
    db.add(service)
    db.flush()
    log_audit(db, actor_user_id=admin.id, tenant_id=None, action="SERVICE_REGISTERED",
               resource=f"service_registry:{service.id}", metadata={"type": payload.type.value})
    db.commit()
    db.refresh(service)
    return service


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: uuid.UUID, db: Session = Depends(get_db), admin=Depends(require_super_admin)):
    db.query(ServiceRegistry).filter(ServiceRegistry.id == service_id).delete()
    log_audit(db, actor_user_id=admin.id, tenant_id=None, action="SERVICE_DEREGISTERED",
               resource=f"service_registry:{service_id}")
    db.commit()
