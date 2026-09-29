from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.control_plane import Tenant, User
from app.models.media import MediaAsset
from app.models.payments import PaymentOrder
from app.models.payments_enums import PaymentOrderStatus
from app.models.system import System
from app.models.tenancy import DemoInstance, ProductionInstance, SystemAccess
from app.models.tenancy_enums import Environment, ProvisioningStatus, SystemAccessStatus, TenantStatus
from app.security.rbac import require_admin_panel

router = APIRouter(prefix="/api/admin/dashboard", tags=["admin", "dashboard"])


class DashboardStats(BaseModel):
    active_tenants: int
    total_users: int
    active_demos: int
    demos_expiring_soon: int
    active_productions: int
    deployed_systems: int
    storage_used_mb: float
    pending_payments: int
    recent_tenants: list[dict]
    recent_demos: list[dict]
    recent_payments: list[dict]


@router.get("", response_model=DashboardStats)
def get_dashboard(db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=7)

    active_tenants = db.execute(
        select(func.count()).select_from(Tenant).where(Tenant.status == TenantStatus.ACTIVE, Tenant.deleted_at.is_(None))
    ).scalar_one()

    total_users = db.execute(select(func.count()).select_from(User).where(User.deleted_at.is_(None))).scalar_one()

    active_demos = db.execute(
        select(func.count()).select_from(SystemAccess)
        .where(SystemAccess.environment == Environment.DEMO, SystemAccess.status == SystemAccessStatus.ACTIVE)
    ).scalar_one()

    demos_expiring_soon = db.execute(
        select(func.count()).select_from(SystemAccess)
        .where(
            SystemAccess.environment == Environment.DEMO, SystemAccess.status == SystemAccessStatus.ACTIVE,
            SystemAccess.expires_at.is_not(None), SystemAccess.expires_at <= soon, SystemAccess.expires_at > now,
        )
    ).scalar_one()

    active_productions = db.execute(
        select(func.count()).select_from(ProductionInstance).where(ProductionInstance.status == ProvisioningStatus.READY)
    ).scalar_one()

    deployed_systems = db.execute(
        select(func.count(func.distinct(SystemAccess.system_id))).select_from(SystemAccess)
        .where(SystemAccess.status == SystemAccessStatus.ACTIVE)
    ).scalar_one()

    storage_used_bytes = db.execute(
        select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(MediaAsset.deleted_at.is_(None))
    ).scalar_one()

    pending_payments = db.execute(
        select(func.count()).select_from(PaymentOrder)
        .where(PaymentOrder.status.in_([PaymentOrderStatus.UNDER_REVIEW, PaymentOrderStatus.PENDING_TRANSFER]))
    ).scalar_one()

    recent_tenants = db.execute(select(Tenant).order_by(Tenant.created_at.desc()).limit(5)).scalars().all()
    recent_demos = db.execute(select(DemoInstance).order_by(DemoInstance.created_at.desc()).limit(5)).scalars().all()
    recent_payments = db.execute(select(PaymentOrder).order_by(PaymentOrder.created_at.desc()).limit(5)).scalars().all()

    return DashboardStats(
        active_tenants=active_tenants,
        total_users=total_users,
        active_demos=active_demos,
        demos_expiring_soon=demos_expiring_soon,
        active_productions=active_productions,
        deployed_systems=deployed_systems,
        storage_used_mb=round(storage_used_bytes / (1024 * 1024), 2),
        pending_payments=pending_payments,
        recent_tenants=[{"id": str(t.id), "display_name": t.display_name, "created_at": t.created_at.isoformat()} for t in recent_tenants],
        recent_demos=[{"id": str(d.id), "tenant_id": str(d.tenant_id), "status": d.status.value, "created_at": d.created_at.isoformat()} for d in recent_demos],
        recent_payments=[{"id": str(p.id), "amount": str(p.amount), "currency": p.currency.value, "status": p.status.value, "created_at": p.created_at.isoformat()} for p in recent_payments],
    )
