import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.db import get_db
from app.core.tokens import hash_token, new_raw_token
from app.models.control_plane import PasswordResetToken, User, UserSession
from app.security.passwords import hash_password
from app.security.rbac import require_admin_panel
from app.security.roles import Role
from app.security.session_auth import CurrentUser, revoke_all_sessions_for_user

router = APIRouter(prefix="/api/admin/users", tags=["admin", "users"])

INVITE_TOKEN_TTL_MINUTES = 30


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    role: Role
    tenant_id: uuid.UUID | None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    role: Role
    tenant_id: uuid.UUID | None = None


class UserCreateResult(BaseModel):
    user: UserOut
    invite_token: str


@router.get("", response_model=list[UserOut])
def list_users(
    tenant_id: uuid.UUID | None = None,
    role: Role | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    stmt = select(User).where(User.deleted_at.is_(None))
    if tenant_id is not None:
        stmt = stmt.where(User.tenant_id == tenant_id)
    if role is not None:
        stmt = stmt.where(User.role == role)
    stmt = stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=UserCreateResult, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    """Crea un usuario e invita (mismo mecanismo de FASE 4: password
    aleatoria descartada + token de reset de un solo uso) -- nunca se le
    asigna ni se conoce una password aca. Si `role` es un rol global
    (ADMIN/SUPPORT/BILLING/SUPER_ADMIN), `tenant_id` debe ser None."""
    email = payload.email.lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un usuario con ese email.")

    from app.security.roles import ADMIN_ROLES
    if payload.role in ADMIN_ROLES and payload.tenant_id is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Un rol administrativo global no puede tener tenant_id.")
    if payload.role not in ADMIN_ROLES and payload.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Un usuario de cliente necesita tenant_id.")

    throwaway_password = new_raw_token(24) + "Aa1"
    user = User(
        email=email, full_name=payload.full_name, password_hash=hash_password(throwaway_password),
        role=payload.role, tenant_id=payload.tenant_id, is_active=True,
    )
    db.add(user)
    db.flush()

    raw_token = new_raw_token(32)
    db.add(PasswordResetToken(
        user_id=user.id, token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=INVITE_TOKEN_TTL_MINUTES),
    ))

    log_audit(db, actor_user_id=admin.id, tenant_id=payload.tenant_id, action="USER_CREATED",
               resource=f"user:{user.id}", metadata={"role": payload.role.value})
    db.commit()
    db.refresh(user)
    return UserCreateResult(user=user, invite_token=raw_token)


@router.post("/{user_id}/disable", response_model=UserOut)
def disable_user(user_id: uuid.UUID, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin_panel())):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No podes deshabilitarte a vos mismo.")

    user.is_active = False
    revoke_all_sessions_for_user(db, user.id)
    log_audit(db, actor_user_id=admin.id, tenant_id=user.tenant_id, action="USER_DISABLED", resource=f"user:{user.id}")
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reactivate", response_model=UserOut)
def reactivate_user(user_id: uuid.UUID, db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")

    user.is_active = True
    log_audit(db, actor_user_id=admin.id, tenant_id=user.tenant_id, action="USER_REACTIVATED", resource=f"user:{user.id}")
    db.commit()
    db.refresh(user)
    return user
