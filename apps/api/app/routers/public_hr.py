"""Marcacion de asistencia desde el celular/tablet de la obra, SIN login.

Autenticacion: token del dispositivo (encabezado X-Device-Token; en la base
solo esta su hash) + identificador propio del aparato (X-Device-Fp, generado
y guardado en su localStorage): el link queda atado al primer aparato.
Proteccion: limite de intentos por IP y por dispositivo, respuestas que no
revelan si una C.I. existe, geocerca si la obra tiene coordenadas, e
idempotencia por client_id para reintentos sin señal."""

import time as _time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.db import get_db
from app.core.net import safe_ip
from app.models.tenancy import SystemAccess
from app.security.erp_context import open_tenant_session
from app.services import hr
from app.services.sales import SalesError
from app.tenant_models.core import Company
from app.tenant_models.hr import HrSite

router = APIRouter(prefix="/api/public/hr/{system_access_id}", tags=["public", "hr"])

_WINDOW_S = 60
_MAX_PER_IP = 120  # una cuadrilla entera marca desde la misma tablet a las 7:00
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(key: str, limit: int = _MAX_PER_IP) -> None:
    """Ventana deslizante en memoria (por proceso). Suficiente para frenar
    adivinanza de C.I. desde un link filtrado; el bloqueo por aparato y la
    respuesta generica hacen el resto."""
    now = _time.monotonic()
    q = _hits[key]
    while q and now - q[0] > _WINDOW_S:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera un minuto.")
    q.append(now)


def _session(system_access_id: uuid.UUID, control_db: Session) -> Session:
    access = control_db.get(SystemAccess, system_access_id)
    if access is None:
        raise HTTPException(status_code=404, detail="Link de marcacion invalido.")
    return open_tenant_session(control_db, access)


class DeviceInfo(BaseModel):
    company: str | None
    site: str
    requires_location: bool


class MarkIn(BaseModel):
    national_id: str = Field(min_length=3, max_length=20)
    client_id: str = Field(min_length=8, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    occurred_at: datetime | None = None


class MarkOut(BaseModel):
    result: str
    employee: str
    time: str
    work_date: str


def _headers(token: str | None, fp: str | None) -> tuple[str, str]:
    if not token or len(token) < 20 or not fp or not 8 <= len(fp) <= 80:
        raise HTTPException(status_code=401, detail="Link de marcacion invalido.")
    return token, fp


@router.post("/device", response_model=DeviceInfo)
def device_info(system_access_id: uuid.UUID, request: Request, control_db: Session = Depends(get_db),
                x_device_token: str | None = Header(default=None), x_device_fp: str | None = Header(default=None)):
    token, fp = _headers(x_device_token, x_device_fp)
    _rate_limit(f"ip:{request.client.host if request.client else '-'}")
    db = _session(system_access_id, control_db)
    try:
        dev = hr.device_for(db, token, fp)
        site = db.get(HrSite, dev.site_id)
        company = db.get(Company, 1)
        db.commit()
        return DeviceInfo(company=company.trade_name or company.legal_name if company else None, site=site.name,
                          requires_location=site.latitude is not None)
    except SalesError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    finally:
        db.close()


@router.post("/mark", response_model=MarkOut)
def mark(system_access_id: uuid.UUID, payload: MarkIn, request: Request, control_db: Session = Depends(get_db),
         x_device_token: str | None = Header(default=None), x_device_fp: str | None = Header(default=None)):
    token, fp = _headers(x_device_token, x_device_fp)
    ip = request.client.host if request.client else "-"
    _rate_limit(f"ip:{ip}")
    _rate_limit(f"tok:{hr.hash_token(token)[:16]}", limit=120)
    db = _session(system_access_id, control_db)
    try:
        dev = hr.device_for(db, token, fp)
        result, a = hr.mark_by_device(db, device=dev, national_id=payload.national_id, client_id=payload.client_id,
                                      latitude=payload.latitude, longitude=payload.longitude, occurred_at=payload.occurred_at)
        db.commit()
        emp = db.get(hr.HrEmployee, a.employee_id)
        t = a.time_out if result == "OUT" or (result == "DUPLICATE" and a.out_client_id == payload.client_id) else a.time_in
        if result != "DUPLICATE":
            access = control_db.get(SystemAccess, system_access_id)
            log_audit(control_db, actor_user_id=None, tenant_id=access.tenant_id, action=f"ERP_HR_DEVICE_MARK_{result}",
                      resource=f"hr_attendance:{a.id}", metadata={"device_id": str(dev.id)},
                      ip_address=safe_ip(ip))
            control_db.commit()
        return MarkOut(result=result, employee=emp.full_name, time=t.strftime("%H:%M"), work_date=a.work_date.isoformat())
    except SalesError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    finally:
        db.close()
