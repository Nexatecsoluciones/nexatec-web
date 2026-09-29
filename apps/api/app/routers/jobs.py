import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.control_center import ProvisioningJob
from app.models.control_center_enums import JobStatus, JobType
from app.models.tenancy_enums import Environment
from app.security.rbac import require_admin_panel
from app.services.provisioning_jobs import run_demo_provisioning_job

router = APIRouter(prefix="/api/admin/jobs", tags=["admin", "jobs"])


class JobOut(BaseModel):
    id: uuid.UUID
    type: JobType
    tenant_id: uuid.UUID
    system_id: uuid.UUID
    environment: Environment
    status: JobStatus
    steps: list
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DemoProvisionJobRequest(BaseModel):
    tenant_id: uuid.UUID
    system_id: uuid.UUID
    duration_days: int = Field(default=14, ge=1, le=90)


@router.get("", response_model=list[JobOut])
def list_jobs(
    status_filter: JobStatus | None = None,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    stmt = select(ProvisioningJob).order_by(ProvisioningJob.created_at.desc()).limit(100)
    if status_filter is not None:
        stmt = stmt.where(ProvisioningJob.status == status_filter)
    return db.execute(stmt).scalars().all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    job = db.get(ProvisioningJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job no encontrado.")
    return job


@router.post("/demo-provision", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_demo_provision_job(
    payload: DemoProvisionJobRequest, db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    """Version con seguimiento de pasos reales de la creacion de demo (ver
    app/services/provisioning_jobs.py). Se ejecuta de forma sincrona --
    para cuando termina el request, el job ya esta en SUCCESS o FAILED con
    todos sus pasos completos; el frontend no necesita hacer polling
    aunque el modelo lo soportaria."""
    job = run_demo_provisioning_job(db, payload.tenant_id, payload.system_id, payload.duration_days, admin.id)
    return job
