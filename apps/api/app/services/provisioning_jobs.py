"""Envuelve app/services/provisioning.py (FASE 4, ya probado) para dejar
un registro de PASOS REALES por job -- nunca un porcentaje inventado. Se
ejecuta de forma SINCRONA dentro del mismo request todavia: no existe un
worker/cola async en el proyecto (Valkey nunca se instalo, ver
docs/ARCHITECTURE.md). Cuando exista, esta funcion es el unico lugar que
habria que mover a un task de background -- el modelo de datos
(ProvisioningJob.steps) ya esta listo para eso. Los pasos registran solo
lo que realmente ocurrio (sin pasos decorativos)."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.models.control_center import ProvisioningJob
from app.models.control_center_enums import JobStatus, JobType
from app.models.control_plane import Tenant
from app.models.system import System
from app.models.tenancy import DemoInstance, SystemAccess
from app.models.tenancy_enums import Environment, ProvisioningStatus, SystemAccessStatus
from app.services import hostname_exposure
from app.services.provisioning import ProvisioningError, provision_tenant_database


def _add_step(job: ProvisioningJob, name: str, ok: bool, detail: str | None = None) -> None:
    job.steps = [*job.steps, {
        "name": name, "status": "success" if ok else "failed", "detail": detail,
        "at": datetime.now(timezone.utc).isoformat(),
    }]


def run_demo_provisioning_job(
    db: Session, tenant_id: uuid.UUID, system_id: uuid.UUID, duration_days: int, actor_user_id: uuid.UUID | None,
) -> ProvisioningJob:
    job = ProvisioningJob(
        type=JobType.DEMO_PROVISION, tenant_id=tenant_id, system_id=system_id,
        environment=Environment.DEMO, status=JobStatus.RUNNING,
        created_by_user_id=actor_user_id, started_at=datetime.now(timezone.utc), steps=[],
    )
    db.add(job)
    db.flush()

    try:
        _add_step(job, "Validando sistema", True)
        system = db.get(System, system_id)
        if system is None or not system.is_active or not system.demo_available:
            raise ValueError("El sistema no admite demos.")

        _add_step(job, "Creando entitlement", True)
        access = db.execute(
            select(SystemAccess)
            .where(SystemAccess.tenant_id == tenant_id, SystemAccess.system_id == system_id, SystemAccess.environment == Environment.DEMO)
            .with_for_update()
        ).scalar_one_or_none()
        if access is not None:
            existing_demo = db.execute(
                select(DemoInstance).where(DemoInstance.system_access_id == access.id)
            ).scalar_one_or_none()
            if existing_demo and existing_demo.status in (ProvisioningStatus.REQUESTED, ProvisioningStatus.PROVISIONING, ProvisioningStatus.READY):
                raise ValueError("Ya existe una demo activa o en curso para este tenant y sistema.")
        else:
            access = SystemAccess(tenant_id=tenant_id, system_id=system_id, environment=Environment.DEMO, status=SystemAccessStatus.PENDING)
            db.add(access)
            db.flush()

        demo = DemoInstance(tenant_id=tenant_id, system_id=system_id, system_access_id=access.id, status=ProvisioningStatus.REQUESTED)
        db.add(demo)
        db.flush()

        tenant_db = provision_tenant_database(db, tenant_id, system_id, Environment.DEMO)
        _add_step(job, "Base de datos creada, esquema migrado y empresa demo cargada", True,
                  f"esquema {tenant_db.schema_version}")

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=duration_days)
        demo.status = ProvisioningStatus.READY
        demo.tenant_database_id = tenant_db.id
        demo.starts_at = now
        demo.expires_at = expires_at
        access.status = SystemAccessStatus.ACTIVE
        access.starts_at = now
        access.expires_at = expires_at

        tenant = db.get(Tenant, tenant_id)
        hostname = hostname_exposure.assign_best_effort(
            db, tenant=tenant, system_id=system_id, environment=Environment.DEMO, prefix="demo-")
        _add_step(job, "Subdominio", hostname is not None, hostname or "no se pudo asignar (el acceso por el portal funciona igual)")
        _add_step(job, "Finalizado", True)

        job.status = JobStatus.SUCCESS
        job.finished_at = datetime.now(timezone.utc)
        log_audit(db, actor_user_id=actor_user_id, tenant_id=tenant_id, action="DEMO_PROVISIONED",
                   resource=f"demo_instance:{demo.id}", metadata={"job_id": str(job.id)})
        db.commit()
        db.refresh(job)
        return job

    except (ProvisioningError, ValueError) as exc:
        _add_step(job, "Error", False, str(exc))
        job.status = JobStatus.FAILED
        job.error_message = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
        return job
