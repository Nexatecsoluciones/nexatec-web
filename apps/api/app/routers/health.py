from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import engine, get_db
from app.models.control_center import ServiceRegistry
from app.models.control_center_enums import ServiceHealthStatus
from app.security.rbac import require_admin_panel

router = APIRouter(prefix="/api/admin/health", tags=["admin", "health"])


class ServiceHealth(BaseModel):
    name: str
    status: str
    detail: str | None = None
    checked_at: str


class HealthReport(BaseModel):
    services: list[ServiceHealth]
    registered_services: list[ServiceHealth]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _check_postgres() -> ServiceHealth:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return ServiceHealth(name="PostgreSQL (control plane)", status="HEALTHY", checked_at=_now())
    except Exception as exc:
        return ServiceHealth(name="PostgreSQL (control plane)", status="DOWN", detail=type(exc).__name__, checked_at=_now())


async def _check_garage() -> ServiceHealth:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(settings.storage_s3_endpoint)
        # Garage responde algo (incluso un error S3 XML) si esta vivo --
        # lo que importa es que la conexion TCP+HTTP haya funcionado.
        status_str = "HEALTHY" if resp.status_code < 500 else "DEGRADED"
        return ServiceHealth(name="Storage (Garage)", status=status_str, checked_at=_now())
    except Exception as exc:
        return ServiceHealth(name="Storage (Garage)", status="DOWN", detail=type(exc).__name__, checked_at=_now())


async def _check_cloudflare_tunnel() -> ServiceHealth:
    """Unica forma honesta de verificar el tunnel desde la propia API: si
    el dominio publico responde, el tunnel+DNS+Cloudflare estan sanos. La
    API no tiene (ni deberia tener) acceso a `systemctl` del host."""
    settings = get_settings()
    if settings.is_development:
        return ServiceHealth(name="Cloudflare Tunnel", status="UNKNOWN", detail="No aplica en development", checked_at=_now())
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get("https://staging.nexatecpy.com/api/health")
        status_str = "HEALTHY" if resp.status_code == 200 else "DEGRADED"
        return ServiceHealth(name="Cloudflare Tunnel", status=status_str, checked_at=_now())
    except Exception as exc:
        return ServiceHealth(name="Cloudflare Tunnel", status="DOWN", detail=type(exc).__name__, checked_at=_now())


@router.get("", response_model=HealthReport)
async def get_health(db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    services = [
        ServiceHealth(name="NEXATEC API", status="HEALTHY", checked_at=_now()),
        await _check_postgres(),
        await _check_garage(),
        await _check_cloudflare_tunnel(),
    ]

    registered = db.execute(select(ServiceRegistry)).scalars().all()
    registered_health = []
    for svc in registered:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://{svc.internal_target}{svc.healthcheck_path}")
            status_str = "HEALTHY" if resp.status_code < 400 else "DEGRADED"
        except Exception:
            status_str = "DOWN"
        registered_health.append(ServiceHealth(name=svc.name, status=status_str, checked_at=_now()))
        svc.status = ServiceHealthStatus(status_str)
        svc.last_checked_at = datetime.now(timezone.utc)
    db.commit()

    return HealthReport(services=services, registered_services=registered_health)
