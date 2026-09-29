import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.db import get_db
from app.core.net import safe_ip
from app.core.tokens import hash_token, new_raw_token
from app.models.control_plane import PasswordResetToken, Tenant, User
from app.models.tenancy import TenantUser
from app.models.tenancy_enums import TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.rbac import require_admin_panel
from app.security.roles import Role

router = APIRouter(prefix="/api/admin/tenants", tags=["admin", "tenants"])

_SLUG_RE = re.compile(r"^[a-z0-9-]{2,80}$")

INVITE_TOKEN_TTL_MINUTES = 30


# --- Schemas explicitos (anti mass-assignment): el cliente NUNCA puede
# setear campos fuera de estos, ni tocar id/created_at/deleted_at/etc. ---


class TenantCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=80)
    legal_name: str = Field(min_length=2, max_length=200)
    display_name: str = Field(min_length=2, max_length=120)
    ruc: str | None = Field(default=None, max_length=30)
    primary_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default="PY", max_length=2)
    city: str | None = Field(default=None, max_length=100)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)

    def validated_slug(self) -> str:
        if not _SLUG_RE.match(self.slug):
            raise ValueError("slug invalido: solo minusculas, numeros y guiones.")
        return self.slug


class TenantUpdate(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=200)
    display_name: str | None = Field(default=None, min_length=2, max_length=120)
    status: TenantStatus | None = None
    ruc: str | None = Field(default=None, max_length=30)
    primary_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=2)
    city: str | None = Field(default=None, max_length=100)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    legal_name: str
    display_name: str
    status: TenantStatus
    ruc: str | None
    primary_email: str | None
    phone: str | None
    country: str | None
    city: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssignTenantUserRequest(BaseModel):
    email: EmailStr
    role: TenantMemberRole
    create_if_missing: bool = False


class TenantUserOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: TenantMemberRole
    status: TenantMemberStatus
    invite_token: str | None = Field(
        default=None,
        description="Solo presente una vez, cuando se crea una cuenta nueva. No se puede recuperar despues.",
    )


def _get_tenant_or_404(db: Session, tenant_id: uuid.UUID) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant no encontrado.")
    return tenant


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    try:
        slug = payload.validated_slug()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El slug ya existe.")

    tenant = Tenant(
        slug=slug,
        legal_name=payload.legal_name,
        display_name=payload.display_name,
        status=TenantStatus.ACTIVE,
        ruc=payload.ruc,
        primary_email=payload.primary_email,
        phone=payload.phone,
        country=payload.country,
        city=payload.city,
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        contact_phone=payload.contact_phone,
    )
    db.add(tenant)
    db.flush()

    log_audit(
        db,
        actor_user_id=admin.id,
        tenant_id=tenant.id,
        action="TENANT_CREATED",
        resource=f"tenant:{tenant.id}",
        ip_address=safe_ip(request.client.host if request.client else None),
    )
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("", response_model=list[TenantOut])
def list_tenants(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    tenants = db.execute(
        select(Tenant).order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return tenants


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: uuid.UUID, db: Session = Depends(get_db), admin=Depends(require_admin_panel())
):
    return _get_tenant_or_404(db, tenant_id)


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    tenant = _get_tenant_or_404(db, tenant_id)

    # Solo los campos explicitos del schema pueden cambiar. model_dump con
    # exclude_unset evita pisar campos no enviados; nunca se usa un
    # model_dump() arbitrario aplicado directo al ORM completo.
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(tenant, field, value)

    log_audit(
        db,
        actor_user_id=admin.id,
        tenant_id=tenant.id,
        action="TENANT_UPDATED",
        resource=f"tenant:{tenant.id}",
        ip_address=safe_ip(request.client.host if request.client else None),
        metadata={"fields": list(updates.keys())},
    )
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/{tenant_id}/users", response_model=list[TenantUserOut])
def list_tenant_users(
    tenant_id: uuid.UUID, db: Session = Depends(get_db), admin=Depends(require_admin_panel())
):
    _get_tenant_or_404(db, tenant_id)
    rows = db.execute(
        select(TenantUser, User.email)
        .join(User, User.id == TenantUser.user_id)
        .where(TenantUser.tenant_id == tenant_id)
    ).all()
    return [
        TenantUserOut(
            id=tu.id, tenant_id=tu.tenant_id, user_id=tu.user_id, email=email,
            role=tu.role, status=tu.status,
        )
        for tu, email in rows
    ]


@router.post("/{tenant_id}/users", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED)
def assign_tenant_user(
    tenant_id: uuid.UUID,
    payload: AssignTenantUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    tenant = _get_tenant_or_404(db, tenant_id)
    email = payload.email.lower()

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    invite_token: str | None = None

    if user is None:
        if not payload.create_if_missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No existe una cuenta con ese email. Reenviar con create_if_missing=true para crearla.",
            )
        # Password aleatoria descartada de inmediato: nadie la conoce, ni
        # nosotros. El usuario la define via el token de invitacion.
        throwaway_password = new_raw_token(24) + "Aa1"
        user = User(
            email=email,
            password_hash=hash_password(throwaway_password),
            role=Role.CLIENT_USER,
            tenant_id=tenant.id,
        )
        db.add(user)
        db.flush()

        raw_token = new_raw_token(32)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=INVITE_TOKEN_TTL_MINUTES),
            )
        )
        invite_token = raw_token

    existing_membership = db.execute(
        select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == user.id)
    ).scalar_one_or_none()

    if existing_membership is not None:
        existing_membership.role = payload.role
        existing_membership.status = TenantMemberStatus.ACTIVE
        membership = existing_membership
    else:
        membership = TenantUser(
            tenant_id=tenant_id, user_id=user.id, role=payload.role, status=TenantMemberStatus.ACTIVE
        )
        db.add(membership)

    db.flush()

    log_audit(
        db,
        actor_user_id=admin.id,
        tenant_id=tenant_id,
        action="USER_ASSIGNED_TO_TENANT",
        resource=f"user:{user.id}",
        ip_address=safe_ip(request.client.host if request.client else None),
        metadata={"role": payload.role.value, "created_user": invite_token is not None},
    )
    db.commit()
    db.refresh(membership)

    return TenantUserOut(
        id=membership.id, tenant_id=membership.tenant_id, user_id=membership.user_id,
        email=email, role=membership.role, status=membership.status, invite_token=invite_token,
    )
