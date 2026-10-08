from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.db import get_db
from app.core.net import safe_ip
from app.models.control_plane import AuditLog, SecurityEvent, User, UserSession
from app.security import totp
from app.security.passwords import WeakPasswordError, hash_password, verify_password
from app.security.roles import ADMIN_ROLES
from app.security.session_auth import (
    STAGE_FULL,
    STAGE_MFA,
    STAGE_PASSWORD_CHANGE,
    CurrentUser,
    get_session_any_stage,
    initial_stage,
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

    # Contrasena correcta: reset de intentos y rotacion de sesion. Si falta
    # MFA o un cambio de contrasena, la sesion nace RESTRINGIDA y solo sirve
    # para completar ese paso (ver app/security/session_auth.py).
    user.failed_login_attempts = 0
    user.locked_until = None
    stage = initial_stage(user)
    db.add(AuditLog(actor_user_id=user.id, tenant_id=user.tenant_id, action="login" if stage == STAGE_FULL else "login_partial",
                    ip_address=client_ip, user_agent=user_agent, metadata_safe={"next": stage}))
    db.commit()

    create_session(db, user, response, client_ip, user_agent, stage=stage)
    return {"email": user.email, "role": user.role.value, "next": stage}


MFA_MAX_ATTEMPTS = 5


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class MfaDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=8)


@router.post("/mfa/verify")
async def mfa_verify(payload: MfaCodeIn, request: Request, db: Session = Depends(get_db),
                     current: tuple = Depends(get_session_any_stage)):
    """Segundo paso del login. 5 codigos errados revocan la sesion pendiente
    (hay que volver a ingresar la contrasena)."""
    db_session, user = current
    if db_session.stage != STAGE_MFA:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No hay verificacion MFA pendiente.")
    client_ip = safe_ip(request.client.host if request.client else None)
    step = totp.verify(decrypt_secret(user.mfa_totp_secret), payload.code, user.mfa_last_step) if user.mfa_totp_secret else None
    if step is None:
        db_session.mfa_failed_attempts += 1
        db.add(SecurityEvent(event_type="mfa_failed", severity="warning", user_id=user.id, ip_address=client_ip,
                             details={"attempts": db_session.mfa_failed_attempts}))
        if db_session.mfa_failed_attempts >= MFA_MAX_ATTEMPTS:
            db_session.revoked_at = datetime.now(timezone.utc)
            db.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Demasiados codigos incorrectos. Volver a iniciar sesion.")
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Codigo incorrecto.")
    user.mfa_last_step = step
    db_session.stage = STAGE_PASSWORD_CHANGE if user.must_change_password else STAGE_FULL
    db.add(AuditLog(actor_user_id=user.id, tenant_id=user.tenant_id, action="mfa_verified", ip_address=client_ip))
    db.commit()
    return {"next": db_session.stage}


@router.post("/change-password")
async def change_password(payload: ChangePasswordIn, request: Request, db: Session = Depends(get_db),
                          current: tuple = Depends(get_session_any_stage)):
    """Sirve con sesion FULL o con la etapa de cambio obligatorio (no con MFA
    pendiente). Revoca todas las OTRAS sesiones del usuario."""
    db_session, user = current
    if db_session.stage == STAGE_MFA:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="STEP_REQUIRED:MFA")
    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contrasena actual no es correcta.")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La nueva contrasena tiene que ser distinta.")
    try:
        user.password_hash = hash_password(payload.new_password)
    except WeakPasswordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    user.must_change_password = False
    now = datetime.now(timezone.utc)
    for other in db.execute(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None),
                                                      UserSession.id != db_session.id)).scalars():
        other.revoked_at = now
    db_session.stage = STAGE_FULL
    db.add(AuditLog(actor_user_id=user.id, tenant_id=user.tenant_id, action="password_changed",
                    ip_address=safe_ip(request.client.host if request.client else None)))
    db.commit()
    return {"next": STAGE_FULL}


@router.post("/mfa/setup")
async def mfa_setup(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Genera un secreto nuevo (todavia NO activo) y devuelve el URI para la
    app autenticadora. Se activa recien con /mfa/enable y un codigo valido."""
    user = db.get(User, current_user.id)
    if user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MFA ya esta activo.")
    secret = totp.generate_secret()
    user.mfa_totp_secret = encrypt_secret(secret)
    user.mfa_last_step = None
    db.commit()
    return {"secret": secret, "otpauth_uri": totp.provisioning_uri(secret, user.email)}


@router.post("/mfa/enable")
async def mfa_enable(payload: MfaCodeIn, request: Request, current_user: CurrentUser = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    user = db.get(User, current_user.id)
    if user.mfa_enabled:
        return {"mfa_enabled": True}
    if not user.mfa_totp_secret:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primero generar el secreto (/mfa/setup).")
    step = totp.verify(decrypt_secret(user.mfa_totp_secret), payload.code, None)
    if step is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Codigo incorrecto: revisar la hora del telefono.")
    user.mfa_enabled = True
    user.mfa_last_step = step
    db.add(AuditLog(actor_user_id=user.id, tenant_id=user.tenant_id, action="mfa_enabled",
                    ip_address=safe_ip(request.client.host if request.client else None)))
    db.commit()
    return {"mfa_enabled": True}


@router.post("/mfa/disable")
async def mfa_disable(payload: MfaDisableIn, request: Request, current_user: CurrentUser = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    user = db.get(User, current_user.id)
    if not user.mfa_enabled:
        return {"mfa_enabled": False}
    if user.role in ADMIN_ROLES and get_settings().require_admin_mfa:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El personal de NEXATEC no puede desactivar MFA.")
    if not verify_password(user.password_hash, payload.password) or totp.verify(
            decrypt_secret(user.mfa_totp_secret), payload.code, user.mfa_last_step) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Contrasena o codigo incorrecto.")
    user.mfa_enabled = False
    user.mfa_totp_secret = None
    user.mfa_last_step = None
    db.add(AuditLog(actor_user_id=user.id, tenant_id=user.tenant_id, action="mfa_disabled",
                    ip_address=safe_ip(request.client.host if request.client else None)))
    db.commit()
    return {"mfa_enabled": False}


@router.post("/logout")
async def logout(
    response: Response,
    nexatec_session: str | None = Cookie(default=None, alias="nexatec_session"),
    current: tuple = Depends(get_session_any_stage),
    db: Session = Depends(get_db),
):
    # Logout real: revoca la sesion en DB, no solo borra la cookie del
    # navegador (si no, el token seguiria siendo valido hasta expirar).
    _, user = current
    if nexatec_session:
        revoke_session_by_raw_cookie(db, nexatec_session)
    db.add(AuditLog(actor_user_id=user.id, tenant_id=user.tenant_id, action="logout"))
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
        "mfa_enabled": current_user.mfa_enabled,
        "mfa_required": current_user.role in ADMIN_ROLES and get_settings().require_admin_mfa,
    }
