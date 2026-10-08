"""Sincroniza la exposicion en Cloudflare de los hostnames de un tenant con
el estado de su acceso. Best-effort siempre: si Cloudflare no esta
configurado o falla, nunca rompe la operacion de negocio que lo llamo. La
fila de `tenant_hostnames` jamas se borra aca -- el subdominio sigue
siendo del tenant aunque deje de resolver."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenancy import SystemAccess, TenantHostname
from app.models.tenancy_enums import Environment, SystemAccessStatus
from app.services import cloudflare_dns


def _safe(fn, hostname: str) -> None:
    try:
        fn(hostname)
    except (cloudflare_dns.CloudflareNotConfiguredError, cloudflare_dns.CloudflareApiError):
        pass


def _hostnames(db: Session, tenant_id: uuid.UUID, system_id: uuid.UUID | None = None,
               environment: Environment | None = None) -> list[TenantHostname]:
    stmt = select(TenantHostname).where(TenantHostname.tenant_id == tenant_id)
    if system_id is not None:
        stmt = stmt.where(TenantHostname.system_id == system_id)
    if environment is not None:
        stmt = stmt.where(TenantHostname.environment == environment)
    return list(db.execute(stmt).scalars().all())


def unexpose(db: Session, *, tenant_id: uuid.UUID, system_id: uuid.UUID | None = None,
             environment: Environment | None = None) -> None:
    for record in _hostnames(db, tenant_id, system_id, environment):
        _safe(cloudflare_dns.remove_public_hostname_route, record.hostname)


def reexpose(db: Session, *, tenant_id: uuid.UUID, system_id: uuid.UUID | None = None,
             environment: Environment | None = None) -> None:
    for record in _hostnames(db, tenant_id, system_id, environment):
        _safe(cloudflare_dns.ensure_public_hostname_route, record.hostname)


def reexpose_active_only(db: Session, *, tenant_id: uuid.UUID) -> None:
    """Al reactivar un tenant: solo vuelve a exponer los hostnames cuyo
    entitlement sigue ACTIVE -- una demo que vencio mientras el tenant
    estaba suspendido no debe volver a resolver."""
    for record in _hostnames(db, tenant_id):
        access = db.execute(
            select(SystemAccess).where(
                SystemAccess.tenant_id == tenant_id,
                SystemAccess.system_id == record.system_id,
                SystemAccess.environment == record.environment,
            )
        ).scalar_one_or_none()
        if access is not None and access.status == SystemAccessStatus.ACTIVE:
            _safe(cloudflare_dns.ensure_public_hostname_route, record.hostname)
