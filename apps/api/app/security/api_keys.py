"""Autenticacion por llave de API (Authorization: Bearer nxk_...).

Solo la usa el ERP (get_erp_user): el Control Center, el portal y el resto
siguen exigiendo sesion de navegador. La llave queda restringida a SU acceso
DEMO (lo verifica get_erp_context)."""

import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.api_keys import ApiKey
from app.models.control_plane import User
from app.security.session_auth import CurrentUser, get_current_user

PREFIX = "nxk_"
RATE_PER_MINUTE = 120
_hits: dict[uuid.UUID, deque] = defaultdict(deque)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_key() -> tuple[str, str]:
    """(valor completo para mostrar UNA vez, prefijo visible)."""
    raw = PREFIX + secrets.token_urlsafe(40)
    return raw, raw[:12]


def issue(db: Session, *, name: str, user_id, system_access_id, created_by_user_id=None, days: int = 90) -> tuple[ApiKey, str]:
    raw, prefix = new_key()
    key = ApiKey(id=uuid.uuid4(), name=name, prefix=prefix, key_hash=hash_key(raw), user_id=user_id,
                 system_access_id=system_access_id, created_by_user_id=created_by_user_id,
                 expires_at=datetime.now(timezone.utc) + timedelta(days=days))
    db.add(key)
    db.flush()
    return key, raw


def _rate_limit(key_id: uuid.UUID) -> None:
    now = time.monotonic()
    q = _hits[key_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MINUTE:
        raise HTTPException(status_code=429, detail=f"Limite de {RATE_PER_MINUTE} pedidos por minuto para esta llave.")
    q.append(now)


def resolve(db: Session, authorization: str) -> CurrentUser:
    raw = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not raw.startswith(PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Llave de API invalida.")
    key = db.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(raw))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if key is None or key.revoked_at is not None or key.expires_at < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Llave de API invalida, revocada o vencida.")
    user = db.get(User, key.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Llave de API invalida.")
    _rate_limit(key.id)
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
        key.last_used_at = now
        db.commit()
    return CurrentUser(id=user.id, tenant_id=user.tenant_id, email=user.email, role=user.role, mfa_enabled=False,
                       api_key_access_id=key.system_access_id, api_key_id=key.id)


async def get_erp_user(
    authorization: str | None = Header(default=None),
    nexatec_session: str | None = Cookie(default=None, alias="nexatec_session"),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """Sesion de navegador o, si no hay, llave de API."""
    if nexatec_session is None and authorization:
        return resolve(db, authorization)
    return await get_current_user(nexatec_session=nexatec_session, db=db)
