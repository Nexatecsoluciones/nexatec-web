import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.db import get_db
from app.core.net import safe_ip
from app.core.tokens import hash_token, new_raw_token
from app.services import email as email_service
from app.models.control_center import DemoRequest
from app.models.control_center_enums import DemoRequestStatus
from app.models.control_plane import PasswordResetToken, Tenant, User
from app.models.tenancy import TenantUser
from app.models.tenancy_enums import TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.rbac import require_admin_panel
from app.security.roles import Role
from app.security.turnstile import verify_turnstile_token
from app.services.provisioning_jobs import run_demo_provisioning_job

# Invitacion por email: el usuario puede abrirla horas despues.
INVITE_TOKEN_TTL_MINUTES = 72 * 60
_SLUG_RE = re.compile(r"^[a-z0-9-]{2,80}$")


def _slugify(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return slug or fallback

router = APIRouter(tags=["demo-requests"])


class DemoRequestCreate(BaseModel):
    contact_name: str
    contact_email: EmailStr
    contact_phone: str | None = None
    company_name: str | None = None
    system_id: uuid.UUID | None = None
    message: str | None = None
    turnstile_token: str


class DemoRequestOut(BaseModel):
    id: uuid.UUID
    contact_name: str
    contact_email: str
    contact_phone: str | None
    company_name: str | None
    system_id: uuid.UUID | None
    message: str | None
    status: DemoRequestStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewDemoRequestPayload(BaseModel):
    note: str | None = None


@router.post("/api/demo-requests", response_model=DemoRequestOut, status_code=status.HTTP_201_CREATED)
async def create_demo_request(payload: DemoRequestCreate, request: Request, db: Session = Depends(get_db)):
    """Publico, sin autenticacion -- por eso exige Turnstile y NUNCA
    aprovisiona nada por si solo. Solo crea el registro de interes; un
    admin decide si se aprueba (ver approve_demo_request)."""
    client_ip = safe_ip(request.client.host if request.client else None)
    if not await verify_turnstile_token(payload.turnstile_token, client_ip):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verificacion anti-bot fallida.")

    demo_request = DemoRequest(
        contact_name=payload.contact_name, contact_email=payload.contact_email,
        contact_phone=payload.contact_phone, company_name=payload.company_name,
        system_id=payload.system_id, message=payload.message, status=DemoRequestStatus.NEW,
    )
    db.add(demo_request)
    db.commit()
    db.refresh(demo_request)
    return demo_request


@router.get("/api/admin/demo-requests", response_model=list[DemoRequestOut])
def list_demo_requests(
    status_filter: DemoRequestStatus | None = None,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    stmt = select(DemoRequest).order_by(DemoRequest.created_at.desc()).limit(100)
    if status_filter is not None:
        stmt = stmt.where(DemoRequest.status == status_filter)
    return db.execute(stmt).scalars().all()


@router.post("/api/admin/demo-requests/{request_id}/contact", response_model=DemoRequestOut)
def mark_contacted(request_id: uuid.UUID, db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    req = db.get(DemoRequest, request_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    req.status = DemoRequestStatus.CONTACTED
    req.reviewed_by_user_id = admin.id
    db.commit()
    db.refresh(req)
    return req


class ApproveDemoRequestPayload(BaseModel):
    duration_days: int = 14


class ApproveDemoRequestResult(BaseModel):
    demo_request: DemoRequestOut
    tenant_id: uuid.UUID
    invite_token: str | None = None
    job_status: str


@router.post("/api/admin/demo-requests/{request_id}/approve", response_model=ApproveDemoRequestResult)
def approve_demo_request(
    request_id: uuid.UUID, payload: ApproveDemoRequestPayload, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    """Unico camino para que una DemoRequest se convierta en infraestructura
    real: crea el tenant (si hace falta), invita al contacto (reusa el
    mecanismo de invite-por-password-reset, igual que en
    app/routers/tenants.py) y dispara el job de provisioning real. Un
    visitante anonimo nunca puede llegar a este punto por su cuenta."""
    demo_req = db.get(DemoRequest, request_id)
    if demo_req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    if demo_req.status not in (DemoRequestStatus.NEW, DemoRequestStatus.CONTACTED):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La solicitud ya fue procesada.")
    if demo_req.system_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La solicitud no indica un sistema.")

    client_ip = safe_ip(request.client.host if request.client else None)

    base_slug = _slugify(demo_req.company_name or demo_req.contact_email.split("@")[0], f"cliente-{uuid.uuid4().hex[:6]}")
    slug = base_slug
    suffix = 1
    while db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none():
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    tenant = Tenant(
        slug=slug,
        legal_name=demo_req.company_name or demo_req.contact_name,
        display_name=demo_req.company_name or demo_req.contact_name,
        status=TenantStatus.ACTIVE,
        contact_name=demo_req.contact_name,
        contact_email=demo_req.contact_email,
        contact_phone=demo_req.contact_phone,
    )
    db.add(tenant)
    db.flush()

    invite_token = None
    user = db.execute(select(User).where(User.email == demo_req.contact_email.lower())).scalar_one_or_none()
    if user is None:
        throwaway_password = new_raw_token(24) + "Aa1"
        user = User(
            email=demo_req.contact_email.lower(), full_name=demo_req.contact_name,
            password_hash=hash_password(throwaway_password), role=Role.CLIENT_USER, tenant_id=tenant.id,
        )
        db.add(user)
        db.flush()
        raw_token = new_raw_token(32)
        db.add(PasswordResetToken(
            user_id=user.id, token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=INVITE_TOKEN_TTL_MINUTES),
        ))
        invite_token = raw_token

    db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role=TenantMemberRole.CLIENT_ADMIN, status=TenantMemberStatus.ACTIVE))

    demo_req.status = DemoRequestStatus.APPROVED
    demo_req.tenant_id = tenant.id
    demo_req.reviewed_by_user_id = admin.id

    log_audit(db, actor_user_id=admin.id, tenant_id=tenant.id, action="DEMO_REQUEST_APPROVED",
               resource=f"demo_request:{demo_req.id}", ip_address=client_ip)
    db.commit()

    job = run_demo_provisioning_job(db, tenant.id, demo_req.system_id, payload.duration_days, admin.id)

    if job.status.value == "SUCCESS":
        demo_req.status = DemoRequestStatus.PROVISIONED
        db.commit()
        # Solo si la demo quedo lista: no invitar a un sistema que no anda.
        if invite_token is not None:
            email_service.send_invitation(user.email, invite_token, INVITE_TOKEN_TTL_MINUTES // 60, tenant.display_name)

    db.refresh(demo_req)
    return ApproveDemoRequestResult(
        demo_request=demo_req, tenant_id=tenant.id, invite_token=invite_token, job_status=job.status.value,
    )


@router.post("/api/admin/demo-requests/{request_id}/reject", response_model=DemoRequestOut)
def reject_demo_request(
    request_id: uuid.UUID, payload: ReviewDemoRequestPayload,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    req = db.get(DemoRequest, request_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    req.status = DemoRequestStatus.REJECTED
    req.reviewed_by_user_id = admin.id
    log_audit(db, actor_user_id=admin.id, tenant_id=None, action="DEMO_REQUEST_REJECTED", resource=f"demo_request:{req.id}")
    db.commit()
    db.refresh(req)
    return req
