import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Response, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models.control_plane import User, UserSession
from app.security.roles import Role

settings = get_settings()
_serializer = URLSafeTimedSerializer(settings.session_secret, salt="nexatec-session")


@dataclass
class CurrentUser:
    id: UUID
    tenant_id: UUID | None
    email: str
    role: Role


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(
    db: Session, user: User, response: Response, ip_address: str | None, user_agent: str | None
) -> None:
    """Crea una sesion nueva (rotacion de sesion tras login) y setea la
    cookie. El valor guardado en DB es el hash del token, nunca el token."""
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.session_max_age_seconds
    )

    db_session = UserSession(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        ip_address=ip_address,
        user_agent=user_agent,
        expires_at=expires_at,
    )
    db.add(db_session)
    db.commit()

    signed_cookie_value = _serializer.dumps({"sid": str(db_session.id), "tok": raw_token})
    response.set_cookie(
        key=settings.session_cookie_name,
        value=signed_cookie_value,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def revoke_session(db: Session, session_id: UUID) -> None:
    db_session = db.get(UserSession, session_id)
    if db_session is not None:
        db_session.revoked_at = datetime.now(timezone.utc)
        db.commit()


def revoke_session_by_raw_cookie(db: Session, cookie_value: str) -> None:
    """Revoca la sesion asociada a una cookie firmada. Usado en logout
    (revocacion real) y puede reusarse para "cerrar sesion en todos lados"."""
    try:
        payload = _serializer.loads(cookie_value, max_age=None)
    except BadSignature:
        return
    raw_token = payload.get("tok")
    if not raw_token:
        return
    db_session = db.execute(
        select(UserSession).where(UserSession.token_hash == _hash_token(raw_token))
    ).scalar_one_or_none()
    if db_session is not None:
        db_session.revoked_at = datetime.now(timezone.utc)
        db.commit()


def revoke_all_sessions_for_user(db: Session, user_id: UUID) -> None:
    """Usado en cambio de password / reset / accion administrativa: invalida
    todas las sesiones activas del usuario de una vez."""
    now = datetime.now(timezone.utc)
    sessions = db.execute(
        select(UserSession).where(
            UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
        )
    ).scalars()
    for s in sessions:
        s.revoked_at = now
    db.commit()


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado."
    )


async def get_current_user(
    nexatec_session: str | None = Cookie(default=None, alias="nexatec_session"),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if nexatec_session is None:
        raise _unauthorized()

    try:
        payload = _serializer.loads(
            nexatec_session, max_age=settings.session_max_age_seconds
        )
    except BadSignature:
        raise _unauthorized()

    raw_token = payload.get("tok")
    if not raw_token:
        raise _unauthorized()

    token_hash = _hash_token(raw_token)
    db_session = db.execute(
        select(UserSession).where(UserSession.token_hash == token_hash)
    ).scalar_one_or_none()

    if db_session is None or db_session.revoked_at is not None:
        raise _unauthorized()
    if db_session.expires_at < datetime.now(timezone.utc):
        raise _unauthorized()

    user = db.get(User, db_session.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise _unauthorized()

    return CurrentUser(id=user.id, tenant_id=user.tenant_id, email=user.email, role=user.role)
