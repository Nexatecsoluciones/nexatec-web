import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services import email as email_service
from app.core.net import safe_ip
from app.models.control_plane import AuditLog, PasswordResetToken, User
from app.security.passwords import WeakPasswordError, hash_password
from app.security.session_auth import revoke_all_sessions_for_user
from app.security.turnstile import verify_turnstile_token

router = APIRouter(prefix="/api/auth/password-reset", tags=["auth"])

RESET_TOKEN_TTL_MINUTES = 30


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class RequestResetPayload(BaseModel):
    email: EmailStr
    turnstile_token: str


class ConfirmResetPayload(BaseModel):
    token: str
    new_password: str


@router.post("/request")
async def request_reset(
    payload: RequestResetPayload, request: Request, db: Session = Depends(get_db)
):
    client_ip = safe_ip(request.client.host if request.client else None)
    if not await verify_turnstile_token(payload.turnstile_token, client_ip):
        raise HTTPException(status_code=400, detail="Verificacion anti-bot fallida.")

    user = db.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalar_one_or_none()

    # Respuesta identica exista o no el usuario: no revelar si un email
    # esta registrado (evita user enumeration).
    if user is not None and user.is_active and user.deleted_at is None:
        raw_token = secrets.token_urlsafe(48)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
            )
        )
        db.commit()
        # Best-effort: sin Brevo configurado no sale nada (y la respuesta es
        # identica igual, para no revelar si el email existe).
        email_service.send_password_reset(user.email, raw_token, RESET_TOKEN_TTL_MINUTES)

    return {"ok": True}


@router.post("/confirm")
async def confirm_reset(payload: ConfirmResetPayload, db: Session = Depends(get_db)):
    token_hash = _hash_token(payload.token)
    reset_token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if (
        reset_token is None
        or reset_token.used_at is not None
        or reset_token.expires_at < now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace de recuperacion es invalido o expiro.",
        )

    user = db.get(User, reset_token.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalido.")

    try:
        user.password_hash = hash_password(payload.new_password)
        user.must_change_password = False
    except WeakPasswordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    reset_token.used_at = now  # token de un solo uso
    user.failed_login_attempts = 0
    user.locked_until = None
    db.add(AuditLog(actor_user_id=user.id, tenant_id=user.tenant_id, action="password_reset"))
    db.commit()

    # Cambiar la password invalida todas las sesiones activas.
    revoke_all_sessions_for_user(db, user.id)

    return {"ok": True}
