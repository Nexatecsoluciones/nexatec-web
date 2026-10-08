"""RR.HH.: configuracion, categorias, obras, personal, asignaciones,
asistencia, horas extra, adelantos, ausencias, lista de bloqueados y
planillas. La logica esta en app/services/hr.py.

Montos (jornal, adelantos, planillas) solo con `hr:amounts`: un supervisor
de obra carga asistencia sin ver lo que gana nadie."""

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, create_model, field_validator
from pydantic.fields import FieldInfo
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, require
from app.services import hr
from app.services.receivables import local_today
from app.services.sales import SalesError
from app.tenant_models.hr import (
    AbsenceKind, AttendanceSource, HrAbsence, HrAdvance, HrAssignment, HrAttendance, HrBlocklist, HrCategory, HrDevice,
    HrEmployee, HrManualOvertime, HrPayroll, HrPayrollOverride, HrSite, HrSiteDaySchedule, OvertimeStatus, PayMethod,
    PayPeriod, PayrollStatus,
)

router = APIRouter(prefix="/api/erp/{system_access_id}/hr", tags=["erp", "hr"])

MAX_PAGE = 500
Money = Field(ge=0, max_digits=18, decimal_places=2)


def _run(ctx: ErpContext, action: str, fn, resource: str, meta=None):
    try:
        obj = fn()
        ctx.db.commit()
    except SalesError as exc:
        ctx.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except IntegrityError as exc:
        ctx.db.rollback()
        msg = str(exc.orig)
        if "uq_hr_employees_ci" in msg:
            raise HTTPException(status_code=409, detail="Ya existe un empleado con esa C.I.")
        if "hr_sites_code_key" in msg or "hr_categories_code_key" in msg:
            raise HTTPException(status_code=409, detail="Ese codigo ya existe.")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Conflicto o dato duplicado.")
    if obj is not None:
        ctx.db.refresh(obj)
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action=action,
              resource=f"{resource}:{getattr(obj, 'id', '')}", metadata=meta(obj) if meta and obj is not None else None)
    ctx.control_db.commit()
    return obj


def _404(what="Recurso"):
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} no encontrado.")


def _get(ctx: ErpContext, model, obj_id, what="Recurso"):
    obj = ctx.db.get(model, obj_id)
    if obj is None:
        raise _404(what)
    return obj


# --- Configuracion y categorias ---------------------------------------------------------


class SettingsIO(BaseModel):
    pay_period: PayPeriod
    workday_hours: Decimal = Field(gt=0, le=24, max_digits=6, decimal_places=2)
    rounding_minutes: int
    overtime_multiplier: Decimal = Field(ge=1, le=3, max_digits=4, decimal_places=2)
    attendance_bonus_enabled: bool
    deduct_ips: bool
    ips_employee_pct: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    ips_employer_pct: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    model_config = ConfigDict(from_attributes=True)

    @field_validator("rounding_minutes")
    @classmethod
    def _round(cls, v):
        if v not in (1, 5, 10, 15, 30):
            raise ValueError("Redondeo: 1, 5, 10, 15 o 30 minutos.")
        return v


@router.get("/settings", response_model=SettingsIO)
def get_settings_(ctx: ErpContext = Depends(require("hr:read"))):
    s = hr.settings(ctx.db)
    ctx.db.commit()
    return s


@router.put("/settings", response_model=SettingsIO)
def put_settings(payload: SettingsIO, ctx: ErpContext = Depends(require("hr:payroll"))):
    def fn():
        s = hr.settings(ctx.db)
        for k, v in payload.model_dump().items():
            setattr(s, k, v)
        return s
    return _run(ctx, "ERP_HR_SETTINGS_UPDATED", fn, "hr_settings", lambda s: payload.model_dump(mode="json"))


class CategoryIn(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    default_trade: str | None = Field(default=None, max_length=100)
    hourly_rate: Decimal = Money
    manual_rate: bool = False


class CategoryOut(CategoryIn):
    id: uuid.UUID
    hourly_rate: Decimal | None = None
    model_config = ConfigDict(from_attributes=True)


def _cat_out(ctx: ErpContext, c: HrCategory) -> CategoryOut:
    out = CategoryOut.model_validate(c)
    if not ctx.can("hr:amounts"):
        out.hourly_rate = None
    return out


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(ctx: ErpContext = Depends(require("hr:read"))):
    return [_cat_out(ctx, c) for c in ctx.db.execute(select(HrCategory).order_by(HrCategory.code)).scalars()]


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(payload: CategoryIn, ctx: ErpContext = Depends(require("hr:payroll"))):
    data = payload.model_dump()
    data["code"] = data["code"].strip().upper()
    return _cat_out(ctx, _run(ctx, "ERP_HR_CATEGORY_CREATED", lambda: _add(ctx, HrCategory(id=uuid.uuid4(), **data)),
                              "hr_category"))


@router.put("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: uuid.UUID, payload: CategoryIn, ctx: ErpContext = Depends(require("hr:payroll"))):
    def fn():
        c = _get(ctx, HrCategory, category_id, "Categoria")
        for k, v in payload.model_dump().items():
            setattr(c, k, v.strip().upper() if k == "code" else v)
        return c
    return _cat_out(ctx, _run(ctx, "ERP_HR_CATEGORY_UPDATED", fn, "hr_category"))


def _add(ctx: ErpContext, obj):
    ctx.db.add(obj)
    ctx.db.flush()
    return obj


# --- Obras ---------------------------------------------------------------------------


class DayScheduleIO(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    model_config = ConfigDict(from_attributes=True)


class SiteIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=2, max_length=160)
    client_name: str | None = Field(default=None, max_length=160)
    location: str | None = Field(default=None, max_length=200)
    start_time: time = time(7, 0)
    end_time: time = time(15, 0)
    workdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], max_length=7)
    tolerance_minutes: int = Field(default=10, ge=0, le=120)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    geofence_radius_m: int = Field(default=200, ge=20, le=5000)
    day_schedules: list[DayScheduleIO] = Field(default_factory=list, max_length=7)
    is_active: bool = True

    @field_validator("workdays")
    @classmethod
    def _wd(cls, v):
        if any(d < 0 or d > 6 for d in v) or len(set(v)) != len(v):
            raise ValueError("Dias: 0 (lunes) a 6 (domingo), sin repetir.")
        return sorted(v)


class SiteOut(SiteIn):
    id: uuid.UUID
    model_config = ConfigDict(from_attributes=True)


def _apply_site(site: HrSite, payload: SiteIn):
    if (payload.latitude is None) != (payload.longitude is None):
        raise SalesError("Latitud y longitud van juntas.")
    if len({d.weekday for d in payload.day_schedules}) != len(payload.day_schedules):
        raise SalesError("Un solo horario especial por dia de la semana.")
    for k, v in payload.model_dump(exclude={"day_schedules"}).items():
        setattr(site, k, v.strip().upper() if k == "code" else v)
    site.day_schedules.clear()
    for d in payload.day_schedules:
        if d.end_time <= d.start_time:
            raise SalesError("Horario especial: la salida tiene que ser posterior a la entrada.")
        site.day_schedules.append(HrSiteDaySchedule(id=uuid.uuid4(), **d.model_dump()))
    return site


@router.get("/sites", response_model=list[SiteOut])
def list_sites(ctx: ErpContext = Depends(require("hr:read")), active: bool | None = None):
    stmt = select(HrSite).order_by(HrSite.code)
    if active is not None:
        stmt = stmt.where(HrSite.is_active.is_(active))
    return list(ctx.db.execute(stmt).scalars())


@router.post("/sites", response_model=SiteOut, status_code=201)
def create_site(payload: SiteIn, ctx: ErpContext = Depends(require("hr:write"))):
    def fn():
        site = HrSite(id=uuid.uuid4())
        ctx.db.add(site)
        _apply_site(site, payload)
        ctx.db.flush()
        return site
    return _run(ctx, "ERP_HR_SITE_CREATED", fn, "hr_site")


@router.put("/sites/{site_id}", response_model=SiteOut)
def update_site(site_id: uuid.UUID, payload: SiteIn, ctx: ErpContext = Depends(require("hr:write"))):
    return _run(ctx, "ERP_HR_SITE_UPDATED", lambda: _apply_site(_get(ctx, HrSite, site_id, "Obra"), payload), "hr_site")


# --- Personal ------------------------------------------------------------------------


class EmployeeBase(BaseModel):
    last_names: str = Field(min_length=2, max_length=120)
    first_names: str = Field(min_length=2, max_length=120)
    trade: str | None = Field(default=None, max_length=100)
    category_id: uuid.UUID | None = None
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    pay_method: PayMethod = PayMethod.CASH
    bank_account: str | None = Field(default=None, max_length=60)
    hourly_rate: Decimal = Money
    bonus_per_hour: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    ips_entry: date | None = None
    ips_exit: date | None = None
    ips_notes: str | None = Field(default=None, max_length=1000)
    address: str | None = Field(default=None, max_length=200)
    neighborhood: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    birth_date: date | None = None
    family_notes: str | None = Field(default=None, max_length=2000)
    training: str | None = Field(default=None, max_length=2000)
    skills: str | None = Field(default=None, max_length=2000)
    references_notes: str | None = Field(default=None, max_length=2000)
    custom_start: time | None = None
    custom_end: time | None = None
    tracks_attendance: bool = True


class EmployeeIn(EmployeeBase):
    national_id: str = Field(min_length=5, max_length=20)
    site_id: uuid.UUID | None = None
    start_date: date | None = None
    hourly_rate: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)


def _all_optional(name: str, base: type[BaseModel], **extra) -> type[BaseModel]:
    """Mismo modelo con todos los campos opcionales (PATCH), conservando las
    validaciones de cada campo."""
    fields = {}
    for fname, f in base.model_fields.items():
        info = FieldInfo.merge_field_infos(f, default=None)
        fields[fname] = (f.annotation | None, info)
    return create_model(name, **fields, **extra)


EmployeeUpdate = _all_optional("EmployeeUpdate", EmployeeBase, is_active=(bool | None, None))


class EmployeeOut(EmployeeBase):
    id: uuid.UUID
    national_id: str
    full_name: str
    is_active: bool
    current_site_id: uuid.UUID | None = None
    hourly_rate: Decimal | None = None
    bonus_per_hour: Decimal | None = None
    bank_account: str | None = None
    model_config = ConfigDict(from_attributes=True)


class EmployeePage(BaseModel):
    total: int
    items: list[EmployeeOut]


def _emp_out(ctx: ErpContext, e: HrEmployee, site_id=None) -> EmployeeOut:
    out = EmployeeOut.model_validate(e)
    out.current_site_id = site_id if site_id is not None else getattr(hr.current_assignment(ctx.db, e.id), "site_id", None)
    if not ctx.can("hr:amounts"):
        out.hourly_rate = out.bonus_per_hour = None
        out.bank_account = None
    return out


@router.get("/employees", response_model=EmployeePage)
def list_employees(
    ctx: ErpContext = Depends(require("hr:read")),
    q: str | None = Query(default=None, max_length=100),
    site_id: uuid.UUID | None = None,
    active: bool | None = True,
    limit: int = Query(default=100, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(HrEmployee, HrAssignment.site_id).outerjoin(
        HrAssignment, (HrAssignment.employee_id == HrEmployee.id) & HrAssignment.end_date.is_(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(HrEmployee.last_names.ilike(like), HrEmployee.first_names.ilike(like),
                              HrEmployee.national_id.ilike(like), HrEmployee.trade.ilike(like)))
    if site_id:
        stmt = stmt.where(HrAssignment.site_id == site_id)
    if active is not None:
        stmt = stmt.where(HrEmployee.is_active.is_(active))
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = ctx.db.execute(stmt.order_by(HrEmployee.last_names, HrEmployee.first_names).limit(limit).offset(offset)).all()
    return {"total": total, "items": [_emp_out(ctx, e, sid) for e, sid in rows]}


@router.get("/employees/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:read"))):
    return _emp_out(ctx, _get(ctx, HrEmployee, employee_id, "Empleado"))


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(payload: EmployeeIn, ctx: ErpContext = Depends(require("hr:write"))):
    data = payload.model_dump(exclude={"site_id", "start_date"})
    if not ctx.can("hr:amounts"):
        data.pop("hourly_rate"), data.pop("bonus_per_hour")
    return _emp_out(ctx, _run(ctx, "ERP_HR_EMPLOYEE_CREATED", lambda: hr.create_employee(
        ctx.db, data, payload.site_id, payload.start_date, ctx.user.id), "hr_employee"))


@router.patch("/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: uuid.UUID, payload: EmployeeUpdate, ctx: ErpContext = Depends(require("hr:write"))):
    changes = payload.model_dump(exclude_unset=True)
    if not ctx.can("hr:amounts") and ({"hourly_rate", "bonus_per_hour", "bank_account"} & changes.keys()):
        raise HTTPException(status_code=403, detail="Tu rol no puede cambiar jornales ni cuentas.")
    if changes.get("is_active") is False:
        raise HTTPException(status_code=422, detail="Para dar de baja usar /deactivate (cierra asignacion e IPS).")
    if ("custom_start" in changes) != ("custom_end" in changes):
        raise HTTPException(status_code=422, detail="El horario individual va completo (entrada y salida).")
    return _emp_out(ctx, _run(ctx, "ERP_HR_EMPLOYEE_UPDATED", lambda: hr.update_employee(ctx.db, employee_id, changes),
                              "hr_employee", lambda e: {"fields": sorted(changes)}))


class ExitIn(BaseModel):
    exit_date: date


@router.post("/employees/{employee_id}/deactivate", response_model=EmployeeOut)
def deactivate_employee(employee_id: uuid.UUID, payload: ExitIn, ctx: ErpContext = Depends(require("hr:write"))):
    return _emp_out(ctx, _run(ctx, "ERP_HR_EMPLOYEE_DEACTIVATED",
                              lambda: hr.deactivate_employee(ctx.db, employee_id, payload.exit_date), "hr_employee"))


class AssignIn(BaseModel):
    site_id: uuid.UUID
    start_date: date
    notes: str | None = Field(default=None, max_length=250)


class AssignmentOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    site_id: uuid.UUID
    start_date: date
    end_date: date | None
    notes: str | None
    model_config = ConfigDict(from_attributes=True)


@router.post("/employees/{employee_id}/assign", response_model=AssignmentOut, status_code=201)
def assign(employee_id: uuid.UUID, payload: AssignIn, ctx: ErpContext = Depends(require("hr:write"))):
    return _run(ctx, "ERP_HR_EMPLOYEE_ASSIGNED", lambda: hr.assign(
        ctx.db, employee_id, payload.site_id, payload.start_date, payload.notes, ctx.user.id), "hr_assignment",
        lambda a: {"site_id": str(a.site_id), "start_date": a.start_date.isoformat()})


@router.get("/employees/{employee_id}/assignments", response_model=list[AssignmentOut])
def assignments(employee_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:read"))):
    return list(ctx.db.execute(select(HrAssignment).where(HrAssignment.employee_id == employee_id)
                               .order_by(HrAssignment.start_date.desc())).scalars())


# --- Asistencia ----------------------------------------------------------------------


class AttendanceIn(BaseModel):
    employee_id: uuid.UUID
    site_id: uuid.UUID
    work_date: date
    time_in: time | None = None
    time_out: time | None = None
    notes: str | None = Field(default=None, max_length=250)


class AttendanceOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    site_id: uuid.UUID
    work_date: date
    time_in: time | None
    time_out: time | None
    regular_hours: Decimal
    overtime_hours: Decimal
    overtime_status: OvertimeStatus
    paid_hours: Decimal
    manual_override: bool
    source: AttendanceSource
    notes: str | None
    employee_name: str | None = None
    model_config = ConfigDict(from_attributes=True)


def _att_out(ctx: ErpContext, rows) -> list[AttendanceOut]:
    ids = {a.employee_id for a in rows}
    names = {i: f"{ln}, {fn}" for i, ln, fn in ctx.db.execute(
        select(HrEmployee.id, HrEmployee.last_names, HrEmployee.first_names).where(HrEmployee.id.in_(ids)))} if ids else {}
    out = []
    for a in rows:
        o = AttendanceOut.model_validate(a)
        o.employee_name = names.get(a.employee_id)
        out.append(o)
    return out


@router.get("/attendance", response_model=list[AttendanceOut])
def list_attendance(
    ctx: ErpContext = Depends(require("hr:read")),
    date_from: date = Query(...),
    date_to: date = Query(...),
    site_id: uuid.UUID | None = None,
    employee_id: uuid.UUID | None = None,
    overtime: OvertimeStatus | None = None,
):
    if (date_to - date_from).days > 62:
        raise HTTPException(status_code=422, detail="Rango maximo: 62 dias.")
    stmt = select(HrAttendance).where(HrAttendance.work_date >= date_from, HrAttendance.work_date <= date_to)
    for col, val in ((HrAttendance.site_id, site_id), (HrAttendance.employee_id, employee_id),
                     (HrAttendance.overtime_status, overtime)):
        if val is not None:
            stmt = stmt.where(col == val)
    return _att_out(ctx, list(ctx.db.execute(stmt.order_by(HrAttendance.work_date.desc())).scalars()))


@router.post("/attendance", response_model=AttendanceOut)
def record_attendance(payload: AttendanceIn, ctx: ErpContext = Depends(require("hr:attendance"))):
    a = _run(ctx, "ERP_HR_ATTENDANCE_RECORDED", lambda: hr.record_attendance(
        ctx.db, source=AttendanceSource.MANUAL, user_id=ctx.user.id, **payload.model_dump()), "hr_attendance",
        lambda a: {"date": a.work_date.isoformat()})
    return _att_out(ctx, [a])[0]


class OverrideHoursIn(BaseModel):
    paid_hours: Decimal = Field(ge=0, le=24, max_digits=6, decimal_places=2)
    reason: str = Field(min_length=3, max_length=250)


@router.post("/attendance/{attendance_id}/override", response_model=AttendanceOut)
def override_hours(attendance_id: uuid.UUID, payload: OverrideHoursIn, ctx: ErpContext = Depends(require("hr:payroll"))):
    a = _run(ctx, "ERP_HR_ATTENDANCE_OVERRIDE", lambda: hr.override_hours(
        ctx.db, attendance_id, payload.paid_hours, payload.reason), "hr_attendance",
        lambda a: {"paid_hours": str(a.paid_hours)})
    return _att_out(ctx, [a])[0]


@router.post("/attendance/{attendance_id}/reset", response_model=AttendanceOut)
def reset_override(attendance_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:payroll"))):
    a = _run(ctx, "ERP_HR_ATTENDANCE_RESET", lambda: hr.reset_override(ctx.db, attendance_id), "hr_attendance")
    return _att_out(ctx, [a])[0]


class DecisionIn(BaseModel):
    approve: bool


@router.post("/attendance/{attendance_id}/overtime", response_model=AttendanceOut)
def decide_overtime(attendance_id: uuid.UUID, payload: DecisionIn, ctx: ErpContext = Depends(require("hr:attendance"))):
    a = _run(ctx, "ERP_HR_OVERTIME_APPROVED" if payload.approve else "ERP_HR_OVERTIME_REJECTED",
             lambda: hr.decide_overtime(ctx.db, attendance_id, payload.approve), "hr_attendance")
    return _att_out(ctx, [a])[0]


class ManualOvertimeIn(BaseModel):
    employee_id: uuid.UUID
    site_id: uuid.UUID
    work_date: date
    hours: Decimal = Field(gt=0, le=24, max_digits=6, decimal_places=2)
    reason: str = Field(min_length=3, max_length=250)


class ManualOvertimeOut(ManualOvertimeIn):
    id: uuid.UUID
    model_config = ConfigDict(from_attributes=True)


@router.post("/overtime", response_model=ManualOvertimeOut, status_code=201)
def manual_overtime(payload: ManualOvertimeIn, ctx: ErpContext = Depends(require("hr:payroll"))):
    return _run(ctx, "ERP_HR_MANUAL_OVERTIME", lambda: hr.add_manual_overtime(
        ctx.db, user_id=ctx.user.id, **payload.model_dump()), "hr_manual_overtime")


@router.get("/overtime", response_model=list[ManualOvertimeOut])
def list_manual_overtime(ctx: ErpContext = Depends(require("hr:read")), date_from: date = Query(...), date_to: date = Query(...)):
    return list(ctx.db.execute(select(HrManualOvertime).where(
        HrManualOvertime.work_date >= date_from, HrManualOvertime.work_date <= date_to)
        .order_by(HrManualOvertime.work_date.desc())).scalars())


# --- Adelantos y ausencias ------------------------------------------------------------


class AdvanceIn(BaseModel):
    employee_id: uuid.UUID
    site_id: uuid.UUID | None = None
    advance_date: date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    pay_method: PayMethod = PayMethod.CASH
    notes: str | None = Field(default=None, max_length=250)


class AdvanceOut(AdvanceIn):
    id: uuid.UUID
    number: str
    voided: bool
    void_reason: str | None
    employee_name: str | None = None
    model_config = ConfigDict(from_attributes=True)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=250)


@router.get("/advances", response_model=list[AdvanceOut])
def list_advances(ctx: ErpContext = Depends(require("hr:amounts")), date_from: date = Query(...), date_to: date = Query(...),
                  employee_id: uuid.UUID | None = None):
    stmt = select(HrAdvance, HrEmployee.last_names, HrEmployee.first_names).join(
        HrEmployee, HrEmployee.id == HrAdvance.employee_id).where(
        HrAdvance.advance_date >= date_from, HrAdvance.advance_date <= date_to)
    if employee_id:
        stmt = stmt.where(HrAdvance.employee_id == employee_id)
    out = []
    for adv, ln, fn in ctx.db.execute(stmt.order_by(HrAdvance.advance_date.desc(), HrAdvance.number.desc())).all():
        o = AdvanceOut.model_validate(adv)
        o.employee_name = f"{ln}, {fn}"
        out.append(o)
    return out


@router.post("/advances", response_model=AdvanceOut, status_code=201)
def create_advance(payload: AdvanceIn, ctx: ErpContext = Depends(require("hr:write"))):
    if not ctx.can("hr:amounts"):
        raise HTTPException(status_code=403, detail="Tu rol no puede registrar adelantos.")
    return _run(ctx, "ERP_HR_ADVANCE_CREATED", lambda: hr.create_advance(ctx.db, user_id=ctx.user.id, **payload.model_dump()),
                "hr_advance", lambda a: {"number": a.number, "amount": str(a.amount)})


@router.post("/advances/{advance_id}/void", response_model=AdvanceOut)
def void_advance(advance_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require("hr:payroll"))):
    return _run(ctx, "ERP_HR_ADVANCE_VOIDED", lambda: hr.void_advance(ctx.db, advance_id, payload.reason), "hr_advance",
                lambda a: {"number": a.number})


class AbsenceIn(BaseModel):
    employee_id: uuid.UUID
    start_date: date
    end_date: date
    kind: AbsenceKind
    notes: str | None = Field(default=None, max_length=250)


class AbsenceOut(AbsenceIn):
    id: uuid.UUID
    model_config = ConfigDict(from_attributes=True)


@router.get("/absences", response_model=list[AbsenceOut])
def list_absences(ctx: ErpContext = Depends(require("hr:read")), date_from: date = Query(...), date_to: date = Query(...)):
    return list(ctx.db.execute(select(HrAbsence).where(HrAbsence.start_date <= date_to, HrAbsence.end_date >= date_from)
                               .order_by(HrAbsence.start_date.desc())).scalars())


@router.post("/absences", response_model=AbsenceOut, status_code=201)
def create_absence(payload: AbsenceIn, ctx: ErpContext = Depends(require("hr:attendance"))):
    return _run(ctx, "ERP_HR_ABSENCE_CREATED", lambda: hr.create_absence(
        ctx.db, employee_id=payload.employee_id, start=payload.start_date, end=payload.end_date, kind=payload.kind,
        notes=payload.notes, user_id=ctx.user.id), "hr_absence")


# --- Lista de bloqueados ---------------------------------------------------------------


class BlockIn(BaseModel):
    national_id: str = Field(min_length=5, max_length=20)
    full_name: str | None = Field(default=None, max_length=180)
    reason: str = Field(min_length=3, max_length=250)


class BlockOut(BlockIn):
    id: uuid.UUID
    included_on: date
    model_config = ConfigDict(from_attributes=True)


@router.get("/blocklist", response_model=list[BlockOut])
def list_blocklist(ctx: ErpContext = Depends(require("hr:write"))):
    return list(ctx.db.execute(select(HrBlocklist).order_by(HrBlocklist.included_on.desc())).scalars())


@router.post("/blocklist", response_model=BlockOut, status_code=201)
def add_block(payload: BlockIn, ctx: ErpContext = Depends(require("hr:write"))):
    return _run(ctx, "ERP_HR_BLOCKLIST_ADDED", lambda: hr.block(ctx.db, payload.national_id, payload.full_name,
                                                                 payload.reason, ctx.user.id), "hr_blocklist")


# --- Dispositivos de marcacion ----------------------------------------------------------


class DeviceIn(BaseModel):
    site_id: uuid.UUID
    name: str = Field(min_length=2, max_length=120)


class DeviceOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    name: str
    is_active: bool
    bound: bool
    last_used_at: datetime | None
    token: str | None = None  # solo en el alta: despues no se puede recuperar


def _dev_out(d: HrDevice, token: str | None = None) -> DeviceOut:
    return DeviceOut(id=d.id, site_id=d.site_id, name=d.name, is_active=d.is_active,
                     bound=d.bound_fingerprint is not None, last_used_at=d.last_used_at, token=token)


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(ctx: ErpContext = Depends(require("hr:write"))):
    return [_dev_out(d) for d in ctx.db.execute(select(HrDevice).order_by(HrDevice.created_at.desc())).scalars()]


@router.post("/devices", response_model=DeviceOut, status_code=201)
def create_device(payload: DeviceIn, ctx: ErpContext = Depends(require("hr:write"))):
    holder: dict = {}

    def fn():
        dev, raw = hr.create_device(ctx.db, payload.site_id, payload.name)
        holder["raw"] = raw
        return dev
    dev = _run(ctx, "ERP_HR_DEVICE_CREATED", fn, "hr_device")
    return _dev_out(dev, holder["raw"])


@router.post("/devices/{device_id}/reset", response_model=DeviceOut)
def reset_device(device_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:write"))):
    def fn():
        d = _get(ctx, HrDevice, device_id, "Dispositivo")
        d.bound_fingerprint = None
        return d
    return _dev_out(_run(ctx, "ERP_HR_DEVICE_RESET", fn, "hr_device"))


@router.post("/devices/{device_id}/disable", response_model=DeviceOut)
def disable_device(device_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:write"))):
    def fn():
        d = _get(ctx, HrDevice, device_id, "Dispositivo")
        d.is_active = False
        return d
    return _dev_out(_run(ctx, "ERP_HR_DEVICE_DISABLED", fn, "hr_device"))


# --- Planillas -----------------------------------------------------------------------


class PayrollIn(BaseModel):
    period_start: date
    period_end: date
    site_id: uuid.UUID | None = None


class PayrollLineOut(BaseModel):
    employee_id: uuid.UUID
    employee_name: str
    national_id: str
    pay_method: PayMethod
    bank_account: str | None
    hourly_rate: Decimal
    regular_hours: Decimal
    overtime_hours: Decimal
    days_worked: int
    unexcused_absences: int
    base_amount: Decimal
    overtime_amount: Decimal
    bonus_amount: Decimal
    adjustment: Decimal
    gross: Decimal
    ips_employee: Decimal
    ips_employer: Decimal
    advances: Decimal
    net: Decimal
    bonus_forced: bool
    adjustment_note: str | None
    model_config = ConfigDict(from_attributes=True)


class PayrollOut(BaseModel):
    id: uuid.UUID
    number: str
    period_start: date
    period_end: date
    site_id: uuid.UUID | None
    status: PayrollStatus
    total_hours: Decimal
    total_gross: Decimal
    total_ips_employee: Decimal
    total_ips_employer: Decimal
    total_advances: Decimal
    total_net: Decimal
    closed_at: datetime | None
    lines: list[PayrollLineOut] = []
    model_config = ConfigDict(from_attributes=True)


class PeriodOut(BaseModel):
    period_start: date
    period_end: date


@router.get("/period", response_model=PeriodOut)
def current_period(ctx: ErpContext = Depends(require("hr:read")), day: date | None = None):
    s, e = hr.period_for(day or local_today(ctx.db), hr.settings(ctx.db))
    return {"period_start": s, "period_end": e}


@router.get("/payroll-preview", response_model=list[PayrollLineOut])
def payroll_preview(ctx: ErpContext = Depends(require("hr:amounts")), period_start: date = Query(...),
                    period_end: date = Query(...), site_id: uuid.UUID | None = None):
    if period_end < period_start or (period_end - period_start).days > 31:
        raise HTTPException(status_code=422, detail="Periodo invalido (maximo un mes).")
    return hr.compute_lines(ctx.db, period_start, period_end, site_id)


@router.get("/payrolls", response_model=list[PayrollOut])
def list_payrolls(ctx: ErpContext = Depends(require("hr:amounts"))):
    items = list(ctx.db.execute(select(HrPayroll).order_by(HrPayroll.period_start.desc(), HrPayroll.number.desc())
                                .limit(100)).scalars())
    return [PayrollOut.model_validate(p).model_copy(update={"lines": []}) for p in items]


@router.get("/payrolls/{payroll_id}", response_model=PayrollOut)
def get_payroll(payroll_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:amounts"))):
    return _get(ctx, HrPayroll, payroll_id, "Planilla")


def _pmeta(p):
    return {"number": p.number, "status": p.status.value, "total_net": str(p.total_net)}


@router.post("/payrolls", response_model=PayrollOut, status_code=201)
def create_payroll(payload: PayrollIn, ctx: ErpContext = Depends(require("hr:payroll"))):
    return _run(ctx, "ERP_HR_PAYROLL_CREATED", lambda: hr.create_payroll(
        ctx.db, payload.period_start, payload.period_end, payload.site_id, ctx.user.id), "hr_payroll", _pmeta)


@router.post("/payrolls/{payroll_id}/recalculate", response_model=PayrollOut)
def recalc_payroll(payroll_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:payroll"))):
    return _run(ctx, "ERP_HR_PAYROLL_RECALCULATED", lambda: hr.recalc_payroll(ctx.db, payroll_id), "hr_payroll", _pmeta)


@router.post("/payrolls/{payroll_id}/close", response_model=PayrollOut)
def close_payroll(payroll_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:payroll"))):
    return _run(ctx, "ERP_HR_PAYROLL_CLOSED", lambda: hr.close_payroll(ctx.db, payroll_id, ctx.user.id), "hr_payroll", _pmeta)


@router.delete("/payrolls/{payroll_id}", status_code=204)
def delete_payroll(payroll_id: uuid.UUID, ctx: ErpContext = Depends(require("hr:payroll"))):
    _run(ctx, "ERP_HR_PAYROLL_DELETED", lambda: hr.delete_draft(ctx.db, payroll_id), "hr_payroll")


class OverrideIn(BaseModel):
    employee_id: uuid.UUID
    period_start: date
    period_end: date
    force_bonus: bool = False
    adjustment: Decimal = Field(default=Decimal("0"), max_digits=18, decimal_places=2)
    reason: str | None = Field(default=None, max_length=250)


class OverrideOut(OverrideIn):
    id: uuid.UUID
    model_config = ConfigDict(from_attributes=True)


@router.put("/payroll-overrides", response_model=OverrideOut)
def set_override(payload: OverrideIn, ctx: ErpContext = Depends(require("hr:payroll"))):
    return _run(ctx, "ERP_HR_PAYROLL_OVERRIDE", lambda: hr.set_override(
        ctx.db, employee_id=payload.employee_id, start=payload.period_start, end=payload.period_end,
        force_bonus=payload.force_bonus, adjustment=payload.adjustment, reason=payload.reason, user_id=ctx.user.id),
        "hr_payroll_override", lambda o: {"adjustment": str(o.adjustment), "force_bonus": o.force_bonus})


@router.get("/payroll-overrides", response_model=list[OverrideOut])
def list_overrides(ctx: ErpContext = Depends(require("hr:amounts")), period_start: date = Query(...), period_end: date = Query(...)):
    return list(ctx.db.execute(select(HrPayrollOverride).where(
        HrPayrollOverride.period_start == period_start, HrPayrollOverride.period_end == period_end)).scalars())
