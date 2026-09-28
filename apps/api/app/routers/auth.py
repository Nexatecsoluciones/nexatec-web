from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.net import safe_ip
from app.models.control_plane import AuditLog, SecurityEvent, User
from app.security.passwords import verify_password
from app.security.session_auth import (
    CurrentUser,
    clear_session_cookie,
    create_session,
    get_current_user,
    revoke_session_by_raw_cookie,
)
from app.security.turnstile import verify_turnstile_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Bloqueo progresivo: minutos de espera segun cantidad de intentos fallidos
# consecutivos. Evita fuerza bruta sin bloquear cuentas indefinidamente.
_LOCKOUT_MINUTES_BY_ATTEMPT = {5: 1, 8: 5, 10: 15, 12: 60}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    turnstile_token: str


def _generic_invalid_credentials() -> HTTPException:
    # Mensaje identico para email inexistente o password incorrecta:
    # no revelar si el email existe (evita user enumeration).
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Email o contrasena incorrectos.",
    )


def _lockout_minutes_for(attempts: int) -> int:
    minutes = 0
    for threshold, mins in sorted(_LOCKOUT_MINUTES_BY_ATTEMPT.items()):
        if attempts >= threshold:
            minutes = mins
    return minutes


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    client_ip = safe_ip(request.client.host if request.client else None)
    user_agent = request.headers.get("user-agent")

    if not await verify_turnstile_token(payload.turnstile_token, client_ip):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verificacion anti-bot fallida.",
        )

    user = db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none()

    if user is None:
        raise _generic_invalid_credentials()

    now = datetime.now(timezone.utc)
    if user.locked_until is not None and user.locked_until > now:
        db.add(
            SecurityEvent(
                event_type="login_blocked_lockout",
                severity="warning",
                user_id=user.id,
                ip_address=client_ip,
            )
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Intente de nuevo mas tarde.",
        )

    if not user.is_active or user.deleted_at is not None or not verify_password(
        user.password_hash, payload.password
    ):
        user.failed_login_attempts += 1
        lockout_minutes = _lockout_minutes_for(user.failed_login_attempts)
        if lockout_minutes:
            user.locked_until = now + timedelta(minutes=lockout_minutes)
        db.add(
            SecurityEvent(
                event_type="login_failed",
                severity="warning",
                user_id=user.id,
                ip_address=client_ip,
                details={"failed_attempts": user.failed_login_attempts},
            )
        )
        db.commit()
        raise _generic_invalid_credentials()

    # Login correcto: reset de intentos fallidos y rotacion de sesion.
    user.failed_login_attempts = 0
    user.locked_until = None
    db.add(AuditLog(actor_user_id=user.id, tenant_id=user.tenant_id, action="login", ip_address=client_ip, user_agent=user_agent))
    db.commit()

    create_session(db, user, response, client_ip, user_agent)
    return {"email": user.email, "role": user.role.value}


@router.post("/logout")
async def logout(
    response: Response,
    nexatec_session: str | None = Cookie(default=None, alias="nexatec_session"),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Logout real: revoca la sesion en DB, no solo borra la cookie del
    # navegador (si no, el token seguiria siendo valido hasta expirar).
    if nexatec_session:
        revoke_session_by_raw_cookie(db, nexatec_session)
    db.add(AuditLog(actor_user_id=current_user.id, tenant_id=current_user.tenant_id, action="logout"))
    db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(current_user: CurrentUser = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "role": current_user.role.value,
        "tenant_id": str(current_user.tenant_id) if current_user.tenant_id else None,
    }
