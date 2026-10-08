import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.db import get_db
from app.core.net import safe_ip
from app.models.control_plane import Tenant
from app.models.system import System
from app.models.tenancy import DemoInstance, ProductionInstance, SystemAccess
from app.models.tenancy_enums import (
    Environment,
    ProvisioningStatus,
    SystemAccessStatus,
    TenantStatus,
)
from app.security.rbac import require_admin_panel
from app.services import hostname_exposure
from app.services.provisioning import ProvisioningError, provision_tenant_database

router = APIRouter(prefix="/api/admin/demos", tags=["admin", "demos"])

DEFAULT_DEMO_DURATION_DAYS = 14


class CreateDemoRequest(BaseModel):
    tenant_id: uuid.UUID
    system_id: uuid.UUID
    duration_days: int = Field(default=DEFAULT_DEMO_DURATION_DAYS, ge=1, le=90)


class RenewDemoRequest(BaseModel):
    extra_days: int = Field(ge=1, le=90)


class DemoInstanceOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    system_id: uuid.UUID
    system_access_id: uuid.UUID
    status: ProvisioningStatus
    starts_at: datetime | None
    expires_at: datetime | None
    # Solo presente cuando create_demo pudo asignar un subdominio (ver
    # hostname_exposure.assign_best_effort). None no significa error -- la demo
    # sigue siendo accesible por el portal con login normal.
    hostname: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ProductionInstanceOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    system_id: uuid.UUID
    system_access_id: uuid.UUID
    status: ProvisioningStatus
    activated_at: datetime | None
    hostname: str | None = None

    model_config = ConfigDict(from_attributes=True)


def _get_demo_or_404(db: Session, demo_id: uuid.UUID) -> DemoInstance:
    demo = db.get(DemoInstance, demo_id)
    if demo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demo no encontrada.")
    return demo


@router.post("", response_model=DemoInstanceOut, status_code=status.HTTP_201_CREATED)
def create_demo(
    payload: CreateDemoRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    tenant = db.get(Tenant, payload.tenant_id)
    if tenant is None or tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant no encontrado o inactivo.")

    system = db.get(System, payload.system_id)
    if system is None or not system.is_active or not system.demo_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El sistema no admite demos."
        )

    client_ip = safe_ip(request.client.host if request.client else None)

    # Lock de fila para evitar que un doble-click cree dos demos: la
    # segunda transaccion espera a que la primera termine (commit/rollback)
    # antes de poder leer el estado actualizado.
    existing_access = db.execute(
        select(SystemAccess)
        .where(
            SystemAccess.tenant_id == payload.tenant_id,
            SystemAccess.system_id == payload.system_id,
            SystemAccess.environment == Environment.DEMO,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if existing_access is not None:
        existing_demo = db.execute(
            select(DemoInstance).where(DemoInstance.system_access_id == existing_access.id)
        ).scalar_one_or_none()
        if existing_demo is not None and existing_demo.status in (
            ProvisioningStatus.REQUESTED,
            ProvisioningStatus.PROVISIONING,
            ProvisioningStatus.READY,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe una demo activa o en curso para este tenant y sistema.",
            )
        access = existing_access
    else:
        access = SystemAccess(
            tenant_id=payload.tenant_id,
            system_id=payload.system_id,
            environment=Environment.DEMO,
            status=SystemAccessStatus.PENDING,
        )
        db.add(access)
        db.flush()
        log_audit(
            db, actor_user_id=admin.id, tenant_id=payload.tenant_id,
            action="SYSTEM_ACCESS_CREATED", resource=f"system_access:{access.id}",
            ip_address=client_ip, metadata={"environment": "DEMO"},
        )

    demo = DemoInstance(
        tenant_id=payload.tenant_id,
        system_id=payload.system_id,
        system_access_id=access.id,
        status=ProvisioningStatus.REQUESTED,
    )
    db.add(demo)
    db.flush()

    log_audit(
        db, actor_user_id=admin.id, tenant_id=payload.tenant_id,
        action="DEMO_REQUESTED", resource=f"demo_instance:{demo.id}", ip_address=client_ip,
    )
    db.commit()
    db.refresh(demo)

    try:
        tenant_db = provision_tenant_database(db, payload.tenant_id, payload.system_id, Environment.DEMO)
    except ProvisioningError:
        demo.status = ProvisioningStatus.FAILED
        db.commit()
        # El entitlement NUNCA queda ACTIVE si el provisioning fallo.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo aprovisionar el entorno de demo. Reintentar mas tarde.",
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=payload.duration_days)

    demo.status = ProvisioningStatus.READY
    demo.tenant_database_id = tenant_db.id
    demo.starts_at = now
    demo.expires_at = expires_at

    access.status = SystemAccessStatus.ACTIVE
    access.starts_at = now
    access.expires_at = expires_at

    hostname = hostname_exposure.assign_best_effort(
        db, tenant=tenant, system_id=payload.system_id, environment=Environment.DEMO, prefix="demo-"
    )

    log_audit(
        db, actor_user_id=admin.id, tenant_id=payload.tenant_id,
        action="DEMO_PROVISIONED", resource=f"demo_instance:{demo.id}", ip_address=client_ip,
        metadata={"hostname": hostname} if hostname else None,
    )
    db.commit()
    db.refresh(demo)
    demo.hostname = hostname  # atributo transiente, no persiste -- solo para la respuesta
    return demo


@router.post("/{demo_id}/renew", response_model=DemoInstanceOut)
def renew_demo(
    demo_id: uuid.UUID,
    payload: RenewDemoRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    demo = _get_demo_or_404(db, demo_id)
    access = db.get(SystemAccess, demo.system_access_id)

    if demo.status != ProvisioningStatus.READY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La demo no esta activa.")

    base = demo.expires_at if demo.expires_at and demo.expires_at > datetime.now(timezone.utc) else datetime.now(timezone.utc)
    new_expiry = base + timedelta(days=payload.extra_days)
    demo.expires_at = new_expiry
    if access is not None:
        access.expires_at = new_expiry
        access.status = SystemAccessStatus.ACTIVE

    # Si la demo habia sido suspendida/expirada antes (hostname sacado de
    # Cloudflare, ver app/services/hostname_exposure.py), renovar la vuelve a
    # exponer con el MISMO subdominio.
    hostname_exposure.reexpose(
        db, tenant_id=demo.tenant_id, system_id=demo.system_id, environment=Environment.DEMO
    )

    log_audit(
        db, actor_user_id=admin.id, tenant_id=demo.tenant_id,
        action="DEMO_RENEWED", resource=f"demo_instance:{demo.id}",
        ip_address=safe_ip(request.client.host if request.client else None),
        metadata={"extra_days": payload.extra_days},
    )
    db.commit()
    db.refresh(demo)
    return demo


@router.post("/{demo_id}/suspend", response_model=DemoInstanceOut)
def suspend_demo(
    demo_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    demo = _get_demo_or_404(db, demo_id)
    access = db.get(SystemAccess, demo.system_access_id)
    if access is not None:
        access.status = SystemAccessStatus.SUSPENDED

    hostname_exposure.unexpose(
        db, tenant_id=demo.tenant_id, system_id=demo.system_id, environment=Environment.DEMO
    )

    log_audit(
        db, actor_user_id=admin.id, tenant_id=demo.tenant_id,
        action="SYSTEM_ACCESS_SUSPENDED", resource=f"demo_instance:{demo.id}",
        ip_address=safe_ip(request.client.host if request.client else None),
    )
    db.commit()
    db.refresh(demo)
    return demo


@router.post("/{demo_id}/expire", response_model=DemoInstanceOut)
def expire_demo(
    demo_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    """Expira manualmente (o via este mismo endpoint llamado por un futuro
    cron/worker). NUNCA hace DROP DATABASE -- solo bloquea acceso."""
    demo = _get_demo_or_404(db, demo_id)
    access = db.get(SystemAccess, demo.system_access_id)
    if access is not None:
        access.status = SystemAccessStatus.EXPIRED

    hostname_exposure.unexpose(
        db, tenant_id=demo.tenant_id, system_id=demo.system_id, environment=Environment.DEMO
    )

    log_audit(
        db, actor_user_id=admin.id, tenant_id=demo.tenant_id,
        action="DEMO_EXPIRED", resource=f"demo_instance:{demo.id}",
        ip_address=safe_ip(request.client.host if request.client else None),
    )
    db.commit()
    db.refresh(demo)
    return demo


def sweep_expired_demos(db: Session) -> int:
    """Barre demos con expires_at vencido y las pasa a EXPIRED, sacando
    tambien su hostname de Cloudflare (best-effort). Disparado por
    nexatec-sweep-expired-demos.timer cada 15 min (ver docs/RUNBOOK.md);
    expuesto tambien via endpoint admin para disparo manual."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(SystemAccess).where(
            SystemAccess.environment == Environment.DEMO,
            SystemAccess.status == SystemAccessStatus.ACTIVE,
            SystemAccess.expires_at.is_not(None),
            SystemAccess.expires_at < now,
        )
    ).scalars().all()

    for access in rows:
        access.status = SystemAccessStatus.EXPIRED
        demo = db.execute(
            select(DemoInstance).where(DemoInstance.system_access_id == access.id)
        ).scalar_one_or_none()
        hostname_exposure.unexpose(
            db, tenant_id=access.tenant_id, system_id=access.system_id, environment=Environment.DEMO
        )
        log_audit(
            db, actor_user_id=None, tenant_id=access.tenant_id,
            action="DEMO_EXPIRED", resource=f"system_access:{access.id}",
            metadata={"reason": "sweep_expired", "demo_instance_id": str(demo.id) if demo else None},
        )
    db.commit()
    return len(rows)


@router.post("/sweep-expired", response_model=dict)
def sweep_expired_demos_endpoint(db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    count = sweep_expired_demos(db)
    return {"expired_count": count}


@router.post("/{demo_id}/convert-to-production", response_model=ProductionInstanceOut)
def convert_demo_to_production(
    demo_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    """Crea una instancia de PRODUCCION nueva y separada -- NUNCA reutiliza
    ni renombra la base de la demo. Sin pagos como requisito todavia (ver
    docs/ARCHITECTURE.md); Billing podra condicionar esto mas adelante."""
    demo = _get_demo_or_404(db, demo_id)
    if demo.status != ProvisioningStatus.READY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Solo se puede convertir una demo lista."
        )

    client_ip = safe_ip(request.client.host if request.client else None)

    existing_prod_access = db.execute(
        select(SystemAccess).where(
            SystemAccess.tenant_id == demo.tenant_id,
            SystemAccess.system_id == demo.system_id,
            SystemAccess.environment == Environment.PRODUCTION,
        )
    ).scalar_one_or_none()
    if existing_prod_access is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un entitlement de produccion para este tenant y sistema.",
        )

    prod_access = SystemAccess(
        tenant_id=demo.tenant_id, system_id=demo.system_id,
        environment=Environment.PRODUCTION, status=SystemAccessStatus.PENDING,
    )
    db.add(prod_access)
    db.flush()

    production = ProductionInstance(
        tenant_id=demo.tenant_id, system_id=demo.system_id,
        system_access_id=prod_access.id, status=ProvisioningStatus.REQUESTED,
    )
    db.add(production)
    db.flush()

    log_audit(
        db, actor_user_id=admin.id, tenant_id=demo.tenant_id,
        action="PRODUCTION_REQUESTED", resource=f"production_instance:{production.id}",
        ip_address=client_ip, metadata={"converted_from_demo": str(demo.id)},
    )
    db.commit()

    try:
        tenant_db = provision_tenant_database(db, demo.tenant_id, demo.system_id, Environment.PRODUCTION)
    except ProvisioningError:
        production.status = ProvisioningStatus.FAILED
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo aprovisionar el entorno de produccion. Reintentar mas tarde.",
        )

    production.status = ProvisioningStatus.READY
    production.tenant_database_id = tenant_db.id
    production.activated_at = datetime.now(timezone.utc)
    prod_access.status = SystemAccessStatus.ACTIVE
    prod_access.starts_at = production.activated_at

    tenant = db.get(Tenant, demo.tenant_id)
    hostname = hostname_exposure.assign_best_effort(
        db, tenant=tenant, system_id=demo.system_id, environment=Environment.PRODUCTION
    )

    log_audit(
        db, actor_user_id=admin.id, tenant_id=demo.tenant_id,
        action="PRODUCTION_PROVISIONED", resource=f"production_instance:{production.id}",
        ip_address=client_ip,
        metadata={"hostname": hostname} if hostname else None,
    )
    db.commit()
    db.refresh(production)
    production.hostname = hostname  # atributo transiente, no persiste -- solo para la respuesta
    return production


@router.get("", response_model=list[DemoInstanceOut])
def list_demos(db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    return db.execute(select(DemoInstance).order_by(DemoInstance.created_at.desc()).limit(100)).scalars().all()
