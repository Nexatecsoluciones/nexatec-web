from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.control_plane import User
from app.security.roles import Role

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/bootstrap-status")
def bootstrap_status(db: Session = Depends(get_db)):
    """Publico, sin autenticacion: solo indica si ya existe al menos un
    SUPER_ADMIN. No revela emails, cantidad exacta ni ningun otro dato --
    el frontend lo usa unicamente para decidir si mostrar el login normal
    o el mensaje de "plataforma aun no inicializada" en /admin."""
    has_super_admin = db.execute(
        select(User.id).where(User.role == Role.SUPER_ADMIN, User.deleted_at.is_(None)).limit(1)
    ).scalar_one_or_none()
    return {"initialized": has_super_admin is not None}
