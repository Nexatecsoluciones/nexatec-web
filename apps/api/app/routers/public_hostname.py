from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.control_plane import Tenant
from app.models.system import System
from app.models.tenancy_enums import Environment
from app.services.hostname_resolution import resolve_request_hostname

router = APIRouter(prefix="/api/public", tags=["public"])


class HostnameContextOut(BaseModel):
    tenant_slug: str
    system_slug: str
    environment: Environment


@router.get("/hostname-context", response_model=HostnameContextOut)
def hostname_context(request: Request, db: Session = Depends(get_db)):
    """Para que el frontend (Next.js) sepa, antes de cualquier login, a
    que tenant/sistema corresponde el subdominio por el que entro el
    visitante. 404 generico para hostname invalido, reservado o
    desconocido -- nunca se distingue el motivo (ver
    app/services/hostname_resolution.py), para no permitir enumerar
    subdominios registrados."""
    resolved = resolve_request_hostname(db, request)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hostname no reconocido.")

    system = db.get(System, resolved.system_id)
    tenant = db.get(Tenant, resolved.tenant_id)

    return HostnameContextOut(
        tenant_slug=tenant.slug,
        system_slug=system.slug,
        environment=resolved.environment,
    )
