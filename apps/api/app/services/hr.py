"""Reglas de RR.HH.: calculo de horas, asistencia, horas extra, adelantos,
ausencias y planillas de pago. Errores con SalesError/InvalidTransition
(422/409) como el resto del ERP.

Calculo de horas de un dia (calc_hours):
- El dia vale `workday_hours` de la empresa (o lo que dure la excepcion de la
  obra para ese dia de la semana, ej. sabado 07:00-12:00 = 5 h). El horario
  de la obra (o el individual del empleado) es solo la REFERENCIA para medir
  tardanza / salida anticipada / extra.
- Tardanza y salida anticipada que superen la tolerancia se descuentan en
  bloques de `rounding_minutes` (al bloque mas cercano).
- Horas extra: solo por quedarse despues de la salida de referencia (llegar
  temprano no suma), en bloques; quedan PENDIENTES hasta que alguien las
  apruebe. Las rechazadas no se pagan.
- Un dia con entrada y sin salida vale 0 (y cuenta como falta), salvo hoy.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from app.services.receivables import local_today
from app.services.sales import InvalidTransition, SalesError, _currency, _q, next_number
from app.tenant_models.hr import (
    AttendanceSource, HrAbsence, HrAdvance, HrAssignment, HrAttendance, HrBlocklist, HrDevice, HrEmployee,
    HrManualOvertime, HrPayroll, HrPayrollLine, HrPayrollOverride, HrSettings, HrSite, OvertimeStatus, PayMethod,
    PayrollStatus,
)

H2 = Decimal("0.01")


def _m(x: Decimal) -> Decimal:
    """Montos siempre con 2 decimales, igual que NUMERIC(18,2) en la base
    (la vista previa y la planilla guardada se ven identicas)."""
    return Decimal(x).quantize(H2)


def settings(db: Session) -> HrSettings:
    s = db.get(HrSettings, 1)
    if s is None:  # bases migradas antes de la fila por defecto
        s = HrSettings(id=1)
        db.add(s)
        db.flush()
    return s


# --- Calculo de horas -----------------------------------------------------------------


def _dt(t: time) -> datetime:
    return datetime.combine(date(2000, 1, 1), t)


def _blocks(minutes: float, size: int) -> int:
    return int(minutes / size + 0.5) if minutes > 0 else 0


def day_exception(site: HrSite, day: date):
    return next((d for d in site.day_schedules if d.weekday == day.weekday()), None)


def is_workday(site: HrSite, day: date) -> bool:
    return day.weekday() in (site.workdays or []) or day_exception(site, day) is not None


def day_value_hours(site: HrSite, day: date, cfg: HrSettings) -> Decimal:
    exc = day_exception(site, day)
    if exc is not None:
        return Decimal((_dt(exc.end_time) - _dt(exc.start_time)).seconds / 3600).quantize(H2)
    return Decimal(cfg.workday_hours)


def reference_schedule(site: HrSite, day: date, employee: HrEmployee | None) -> tuple[time, time]:
    exc = day_exception(site, day)
    if exc is not None:
        return exc.start_time, exc.end_time  # el horario individual no aplica a dias de excepcion
    if employee is not None and employee.custom_start and employee.custom_end:
        return employee.custom_start, employee.custom_end
    return site.start_time, site.end_time


def calc_hours(time_in: time | None, time_out: time | None, site: HrSite, day: date, cfg: HrSettings,
               employee: HrEmployee | None = None) -> tuple[Decimal, Decimal]:
    """(horas_normales, horas_extra) del dia."""
    if not time_in or not time_out:
        return Decimal(0), Decimal(0)
    ref_in, ref_out = reference_schedule(site, day, employee)
    e, s = _dt(time_in), _dt(time_out)
    if s < e:
        s += timedelta(days=1)  # turno que pasa la medianoche
    pe, ps = _dt(ref_in), _dt(ref_out)
    if ps < pe:
        ps += timedelta(days=1)
    size, tol = cfg.rounding_minutes, site.tolerance_minutes
    late = max(0.0, (e - pe).total_seconds() / 60 - tol)
    early = max(0.0, (ps - s).total_seconds() / 60 - tol)
    regular = day_value_hours(site, day, cfg) - Decimal((_blocks(late, size) + _blocks(early, size)) * size) / 60
    overtime = Decimal(_blocks(max(0.0, (s - ps).total_seconds() / 60), size) * size) / 60
    return max(Decimal(0), regular).quantize(H2), overtime.quantize(H2)


def day_counts_as_present(a: HrAttendance, today: date) -> bool:
    if a.time_in and a.time_out:
        return True
    if a.paid_hours > 0:
        return True  # horas cargadas a mano
    return bool(a.time_in and a.work_date >= today)


# --- Obras y personal -----------------------------------------------------------------


def _get(db: Session, model, obj_id, what: str, lock: bool = False):
    stmt = select(model).where(model.id == obj_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    obj = db.execute(stmt).scalar_one_or_none()
    if obj is None:
        raise SalesError(f"{what} inexistente.")
    return obj


def active_site(db: Session, site_id) -> HrSite:
    site = _get(db, HrSite, site_id, "Obra")
    if not site.is_active:
        raise SalesError("La obra esta inactiva.")
    return site


def normalize_ci(ci: str) -> str:
    clean = "".join(ch for ch in ci if ch.isalnum()).upper()
    if not 5 <= len(clean) <= 20:
        raise SalesError("C.I. invalida.")
    return clean


def is_blocked(db: Session, ci: str) -> bool:
    return db.execute(select(func.count()).select_from(HrBlocklist).where(HrBlocklist.national_id == ci)).scalar_one() > 0


def create_employee(db: Session, data: dict, site_id, start_date: date | None, user_id) -> HrEmployee:
    data = dict(data)
    data["national_id"] = normalize_ci(data["national_id"])
    if is_blocked(db, data["national_id"]):
        raise InvalidTransition("Esa C.I. esta en la lista de bloqueados: no se puede dar de alta.")
    if data.get("category_id") and not data.get("hourly_rate"):
        from app.tenant_models.hr import HrCategory
        cat = _get(db, HrCategory, data["category_id"], "Categoria")
        if not cat.manual_rate:
            data["hourly_rate"] = cat.hourly_rate
        data.setdefault("trade", cat.default_trade)
    emp = HrEmployee(id=uuid.uuid4(), **data)
    db.add(emp)
    db.flush()
    if site_id:
        assign(db, emp.id, site_id, start_date or local_today(db), None, user_id)
    return emp


def update_employee(db: Session, employee_id, changes: dict) -> HrEmployee:
    emp = _get(db, HrEmployee, employee_id, "Empleado", lock=True)
    if changes.get("is_active") is True and not emp.is_active and is_blocked(db, emp.national_id):
        raise InvalidTransition("Esa C.I. esta en la lista de bloqueados: no se puede reactivar.")
    for k, v in changes.items():
        setattr(emp, k, v)
    return emp


def current_assignment(db: Session, employee_id) -> HrAssignment | None:
    return db.execute(select(HrAssignment).where(HrAssignment.employee_id == employee_id,
                                                 HrAssignment.end_date.is_(None))).scalar_one_or_none()


def assign(db: Session, employee_id, site_id, start: date, notes: str | None, user_id) -> HrAssignment:
    """Asigna (o traslada) a una obra: cierra la asignacion abierta el dia
    anterior y abre la nueva. Queda todo el historial."""
    emp = _get(db, HrEmployee, employee_id, "Empleado", lock=True)
    if not emp.is_active:
        raise InvalidTransition("El empleado esta inactivo.")
    active_site(db, site_id)
    cur = current_assignment(db, employee_id)
    if cur is not None:
        if cur.site_id == site_id:
            raise InvalidTransition("Ya esta asignado a esa obra.")
        if start <= cur.start_date:
            raise SalesError("La fecha del traslado tiene que ser posterior al inicio de la asignacion actual.")
        cur.end_date = start - timedelta(days=1)
        db.flush()
    a = HrAssignment(id=uuid.uuid4(), employee_id=employee_id, site_id=site_id, start_date=start, notes=notes,
                     created_by_user_id=user_id)
    db.add(a)
    db.flush()
    return a


def deactivate_employee(db: Session, employee_id, exit_date: date) -> HrEmployee:
    emp = _get(db, HrEmployee, employee_id, "Empleado", lock=True)
    emp.is_active = False
    if emp.ips_entry and not emp.ips_exit:
        emp.ips_exit = max(exit_date, emp.ips_entry)
    cur = current_assignment(db, employee_id)
    if cur is not None:
        cur.end_date = max(exit_date, cur.start_date)
    return emp


def block(db: Session, ci: str, full_name: str | None, reason: str, user_id) -> HrBlocklist:
    ci = normalize_ci(ci)
    row = HrBlocklist(id=uuid.uuid4(), national_id=ci, full_name=full_name, reason=reason,
                      included_on=local_today(db), created_by_user_id=user_id)
    db.add(row)
    emp = db.execute(select(HrEmployee).where(HrEmployee.national_id == ci)).scalar_one_or_none()
    if emp is not None and emp.is_active:
        deactivate_employee(db, emp.id, local_today(db))
    db.flush()
    return row


# --- Asistencia -----------------------------------------------------------------------


def _recompute(db: Session, a: HrAttendance, cfg: HrSettings) -> None:
    if a.manual_override:
        return
    site = db.get(HrSite, a.site_id)
    emp = db.get(HrEmployee, a.employee_id)
    regular, overtime = calc_hours(a.time_in, a.time_out, site, a.work_date, cfg, emp)
    a.regular_hours = regular
    if overtime != a.overtime_hours:
        # Cambiaron las marcas: la decision anterior sobre la extra no vale.
        a.overtime_status = OvertimeStatus.PENDING if overtime > 0 else OvertimeStatus.NONE
    elif overtime == 0:
        a.overtime_status = OvertimeStatus.NONE
    a.overtime_hours = overtime
    a.paid_hours = regular + (overtime if a.overtime_status == OvertimeStatus.APPROVED else Decimal(0))


def record_attendance(db: Session, *, employee_id, site_id, work_date: date, time_in: time | None,
                      time_out: time | None, source: AttendanceSource, user_id, notes: str | None = None,
                      device_id=None) -> HrAttendance:
    """Alta o correccion del registro del dia (upsert por empleado+obra+dia)."""
    if work_date > local_today(db):
        raise SalesError("No se registra asistencia de dias futuros.")
    if time_in is None and time_out is not None:
        raise SalesError("No hay salida sin entrada.")
    emp = _get(db, HrEmployee, employee_id, "Empleado")
    if not emp.is_active:
        raise InvalidTransition("El empleado esta inactivo.")
    active_site(db, site_id)
    cfg = settings(db)
    a = db.execute(select(HrAttendance).where(
        HrAttendance.employee_id == employee_id, HrAttendance.site_id == site_id, HrAttendance.work_date == work_date,
    ).with_for_update()).scalar_one_or_none()
    if a is None:
        a = HrAttendance(id=uuid.uuid4(), employee_id=employee_id, site_id=site_id, work_date=work_date, source=source,
                         overtime_status=OvertimeStatus.NONE, regular_hours=0, overtime_hours=0, paid_hours=0,
                         created_by_user_id=user_id, device_id=device_id)
        db.add(a)
    _guard_closed_period(db, employee_id, work_date)
    a.time_in, a.time_out = time_in, time_out
    if notes is not None:
        a.notes = notes
    a.manual_override = False
    _recompute(db, a, cfg)
    db.flush()
    return a


def override_hours(db: Session, attendance_id, paid_hours: Decimal, reason: str) -> HrAttendance:
    """RR.HH. fija a mano las horas pagas de un dia (queda protegido de
    recalculos hasta que se restablezca)."""
    a = _get(db, HrAttendance, attendance_id, "Registro", lock=True)
    _guard_closed_period(db, a.employee_id, a.work_date)
    a.paid_hours = paid_hours.quantize(H2)
    a.manual_override = True
    a.notes = reason
    return a


def reset_override(db: Session, attendance_id) -> HrAttendance:
    a = _get(db, HrAttendance, attendance_id, "Registro", lock=True)
    _guard_closed_period(db, a.employee_id, a.work_date)
    a.manual_override = False
    _recompute(db, a, settings(db))
    return a


def decide_overtime(db: Session, attendance_id, approve: bool) -> HrAttendance:
    a = _get(db, HrAttendance, attendance_id, "Registro", lock=True)
    if a.overtime_hours <= 0:
        raise InvalidTransition("Ese dia no tiene horas extra.")
    _guard_closed_period(db, a.employee_id, a.work_date)
    a.overtime_status = OvertimeStatus.APPROVED if approve else OvertimeStatus.REJECTED
    if not a.manual_override:
        a.paid_hours = a.regular_hours + (a.overtime_hours if approve else Decimal(0))
    return a


def add_manual_overtime(db: Session, *, employee_id, site_id, work_date: date, hours: Decimal, reason: str,
                        user_id) -> HrManualOvertime:
    _get(db, HrEmployee, employee_id, "Empleado")
    active_site(db, site_id)
    if work_date > local_today(db):
        raise SalesError("No se cargan horas de dias futuros.")
    _guard_closed_period(db, employee_id, work_date)
    row = HrManualOvertime(id=uuid.uuid4(), employee_id=employee_id, site_id=site_id, work_date=work_date,
                           hours=hours.quantize(H2), reason=reason, created_by_user_id=user_id)
    db.add(row)
    db.flush()
    return row


# --- Dispositivos de marcacion ----------------------------------------------------------


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_device(db: Session, site_id, name: str) -> tuple[HrDevice, str]:
    active_site(db, site_id)
    raw = secrets.token_urlsafe(32)
    dev = HrDevice(id=uuid.uuid4(), name=name, site_id=site_id, token_hash=hash_token(raw))
    db.add(dev)
    db.flush()
    return dev, raw


# --- Adelantos y ausencias ------------------------------------------------------------


def create_advance(db: Session, *, employee_id, site_id, advance_date: date, amount: Decimal, pay_method: PayMethod,
                   notes: str | None, user_id) -> HrAdvance:
    emp = _get(db, HrEmployee, employee_id, "Empleado")
    if not emp.is_active:
        raise InvalidTransition("El empleado esta inactivo.")
    if advance_date > local_today(db):
        raise SalesError("No se registran adelantos con fecha futura.")
    _guard_closed_period(db, employee_id, advance_date)
    adv = HrAdvance(id=uuid.uuid4(), number=next_number(db, "HR_ADVANCE"), employee_id=employee_id, site_id=site_id,
                    advance_date=advance_date, amount=amount, pay_method=pay_method, notes=notes,
                    created_by_user_id=user_id)
    db.add(adv)
    db.flush()
    return adv


def void_advance(db: Session, advance_id, reason: str) -> HrAdvance:
    adv = _get(db, HrAdvance, advance_id, "Adelanto", lock=True)
    if adv.voided:
        raise InvalidTransition("El adelanto ya estaba anulado.")
    _guard_closed_period(db, adv.employee_id, adv.advance_date)
    adv.voided = True
    adv.void_reason = reason
    return adv


def create_absence(db: Session, *, employee_id, start: date, end: date, kind, notes: str | None, user_id) -> HrAbsence:
    _get(db, HrEmployee, employee_id, "Empleado")
    if end < start:
        raise SalesError("La fecha final es anterior a la inicial.")
    row = HrAbsence(id=uuid.uuid4(), employee_id=employee_id, start_date=start, end_date=end, kind=kind, notes=notes,
                    created_by_user_id=user_id)
    db.add(row)
    db.flush()
    return row


# --- Planillas ------------------------------------------------------------------------


def _guard_closed_period(db: Session, employee_id, day: date) -> None:
    """Nada que cambie lo pagado puede tocar un periodo ya cerrado para ese
    empleado."""
    closed = db.execute(
        select(HrPayroll.number).join(HrPayrollLine, HrPayrollLine.payroll_id == HrPayroll.id).where(
            HrPayroll.status == PayrollStatus.CLOSED, HrPayrollLine.employee_id == employee_id,
            HrPayroll.period_start <= day, HrPayroll.period_end >= day,
        ).limit(1)
    ).scalar_one_or_none()
    if closed:
        raise InvalidTransition(f"Ese dia ya esta liquidado en la planilla {closed} (cerrada).")


def period_for(day: date, cfg: HrSettings) -> tuple[date, date]:
    first = day.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    if cfg.pay_period.value == "MONTHLY":
        return first, last
    return (first, day.replace(day=15)) if day.day <= 15 else (day.replace(day=16), last)


@dataclass
class _Acc:
    regular: Decimal = Decimal(0)
    overtime: Decimal = Decimal(0)
    days: int = 0


def _employees_in_period(db: Session, start: date, end: date, site_id) -> list[tuple[HrEmployee, HrAssignment]]:
    """Empleados con asignacion vigente en algun momento del periodo (si se
    filtra por obra, solo los de esa obra). La asignacion devuelta es la
    ultima del periodo (de ella se toma la obra de referencia)."""
    stmt = select(HrAssignment).where(HrAssignment.start_date <= end,
                                      or_(HrAssignment.end_date.is_(None), HrAssignment.end_date >= start))
    if site_id:
        stmt = stmt.where(HrAssignment.site_id == site_id)
    latest: dict[uuid.UUID, HrAssignment] = {}
    for a in db.execute(stmt.order_by(HrAssignment.start_date)).scalars():
        latest[a.employee_id] = a
    emps = {e.id: e for e in db.execute(select(HrEmployee).where(HrEmployee.id.in_(latest))).scalars()} if latest else {}
    return sorted(((emps[k], v) for k, v in latest.items()), key=lambda x: x[0].full_name)


def compute_lines(db: Session, start: date, end: date, site_id=None) -> list[dict]:
    cfg = settings(db)
    q = _q(_currency(db).decimals)
    today = local_today(db)
    rows = []
    pct_emp = Decimal(cfg.ips_employee_pct) / 100
    pct_pat = Decimal(cfg.ips_employer_pct) / 100
    for emp, assignment in _employees_in_period(db, start, end, site_id):
        site = db.get(HrSite, assignment.site_id)
        att_stmt = select(HrAttendance).where(HrAttendance.employee_id == emp.id, HrAttendance.work_date >= start,
                                              HrAttendance.work_date <= end)
        if site_id:
            att_stmt = att_stmt.where(HrAttendance.site_id == site_id)
        acc = _Acc()
        present: set[date] = set()
        for a in db.execute(att_stmt).scalars():
            if a.manual_override:
                acc.regular += a.paid_hours
            else:
                acc.regular += a.regular_hours
                if a.overtime_status == OvertimeStatus.APPROVED:
                    acc.overtime += a.overtime_hours
            if day_counts_as_present(a, today):
                present.add(a.work_date)
        mo_stmt = select(func.coalesce(func.sum(HrManualOvertime.hours), 0)).where(
            HrManualOvertime.employee_id == emp.id, HrManualOvertime.work_date >= start, HrManualOvertime.work_date <= end)
        if site_id:
            mo_stmt = mo_stmt.where(HrManualOvertime.site_id == site_id)
        acc.overtime += Decimal(db.execute(mo_stmt).scalar_one())

        # Faltas injustificadas: dias habiles de su obra (dentro de su
        # asignacion y hasta hoy) sin asistencia ni ausencia justificada.
        absences = list(db.execute(select(HrAbsence).where(
            HrAbsence.employee_id == emp.id, HrAbsence.start_date <= end, HrAbsence.end_date >= start)).scalars())
        unexcused = 0
        if emp.tracks_attendance:
            d = max(start, assignment.start_date)
            last = min(end, assignment.end_date or end, today)
            while d <= last:
                if is_workday(site, d) and d not in present and not any(x.start_date <= d <= x.end_date for x in absences):
                    unexcused += 1
                d += timedelta(days=1)

        ov = db.execute(select(HrPayrollOverride).where(
            HrPayrollOverride.employee_id == emp.id, HrPayrollOverride.period_start == start,
            HrPayrollOverride.period_end == end)).scalar_one_or_none()
        forced = bool(ov and ov.force_bonus)
        adjustment = (ov.adjustment if ov else Decimal(0)).quantize(q, ROUND_HALF_UP)

        rate = Decimal(emp.hourly_rate)
        base = (acc.regular * rate).quantize(q, ROUND_HALF_UP)
        ot_amount = (acc.overtime * rate * Decimal(cfg.overtime_multiplier)).quantize(q, ROUND_HALF_UP)
        bonus = Decimal(0)
        if cfg.attendance_bonus_enabled and (unexcused == 0 or forced):
            bonus = ((acc.regular + acc.overtime) * Decimal(emp.bonus_per_hour)).quantize(q, ROUND_HALF_UP)
        gross = base + ot_amount + bonus + adjustment
        in_ips = cfg.deduct_ips and emp.ips_entry is not None and emp.ips_entry <= end and (
            emp.ips_exit is None or emp.ips_exit >= start)
        ips_e = (gross * pct_emp).quantize(q, ROUND_HALF_UP) if in_ips and gross > 0 else Decimal(0)
        ips_p = (gross * pct_pat).quantize(q, ROUND_HALF_UP) if in_ips and gross > 0 else Decimal(0)
        adv = Decimal(db.execute(select(func.coalesce(func.sum(HrAdvance.amount), 0)).where(
            HrAdvance.employee_id == emp.id, HrAdvance.voided.is_(False), HrAdvance.advance_date >= start,
            HrAdvance.advance_date <= end)).scalar_one()).quantize(q, ROUND_HALF_UP)
        if site_id and assignment.site_id != site_id:
            adv = Decimal(0)  # los adelantos van a la obra actual del periodo, nunca duplicados
        rows.append({
            "employee_id": emp.id, "employee_name": emp.full_name, "national_id": emp.national_id,
            "pay_method": emp.pay_method, "bank_account": emp.bank_account, "hourly_rate": _m(rate),
            "regular_hours": acc.regular.quantize(H2), "overtime_hours": acc.overtime.quantize(H2),
            "days_worked": len(present), "unexcused_absences": unexcused, "base_amount": _m(base),
            "overtime_amount": _m(ot_amount), "bonus_amount": _m(bonus), "adjustment": _m(adjustment), "gross": _m(gross),
            "ips_employee": _m(ips_e), "ips_employer": _m(ips_p), "advances": _m(adv), "net": _m(gross - ips_e - adv),
            "bonus_forced": forced, "adjustment_note": ov.reason if ov else None,
        })
    return rows


def _fill(db: Session, p: HrPayroll) -> HrPayroll:
    p.lines.clear()
    db.flush()
    lines = compute_lines(db, p.period_start, p.period_end, p.site_id)
    for r in lines:
        p.lines.append(HrPayrollLine(id=uuid.uuid4(), **r))
    p.total_hours = sum((r["regular_hours"] + r["overtime_hours"] for r in lines), Decimal(0))
    for field, key in (("total_gross", "gross"), ("total_ips_employee", "ips_employee"),
                       ("total_ips_employer", "ips_employer"), ("total_advances", "advances"), ("total_net", "net")):
        setattr(p, field, sum((r[key] for r in lines), Decimal(0)))
    db.flush()
    return p


def create_payroll(db: Session, start: date, end: date, site_id, user_id) -> HrPayroll:
    if end < start:
        raise SalesError("El periodo termina antes de empezar.")
    if (end - start).days > 31:
        raise SalesError("Un periodo de pago no puede superar un mes.")
    if site_id:
        _get(db, HrSite, site_id, "Obra")
    overlap = db.execute(select(HrPayroll.number).where(
        HrPayroll.period_start <= end, HrPayroll.period_end >= start,
        or_(HrPayroll.site_id.is_(None), HrPayroll.site_id == site_id) if site_id else true(),
    ).limit(1)).scalar_one_or_none()
    if overlap:
        raise InvalidTransition(f"Ya existe la planilla {overlap} que se superpone con ese periodo.")
    p = HrPayroll(id=uuid.uuid4(), number=next_number(db, "HR_PAYROLL"), period_start=start, period_end=end,
                  site_id=site_id, status=PayrollStatus.DRAFT, created_by_user_id=user_id)
    db.add(p)
    db.flush()
    return _fill(db, p)


def _lock_draft(db: Session, payroll_id) -> HrPayroll:
    p = _get(db, HrPayroll, payroll_id, "Planilla", lock=True)
    if p.status != PayrollStatus.DRAFT:
        raise InvalidTransition("La planilla esta cerrada.")
    return p


def recalc_payroll(db: Session, payroll_id) -> HrPayroll:
    return _fill(db, _lock_draft(db, payroll_id))


def close_payroll(db: Session, payroll_id, user_id) -> HrPayroll:
    p = _lock_draft(db, payroll_id)
    if p.period_end >= local_today(db):
        raise InvalidTransition("No se cierra un periodo que todavia no termino.")
    pending = db.execute(select(func.count()).select_from(HrAttendance).where(
        HrAttendance.work_date >= p.period_start, HrAttendance.work_date <= p.period_end,
        HrAttendance.overtime_status == OvertimeStatus.PENDING,
        HrAttendance.site_id == p.site_id if p.site_id else true())).scalar_one()
    if pending:
        raise InvalidTransition(f"Hay {pending} dia(s) con horas extra pendientes de aprobar o rechazar.")
    _fill(db, p)  # se cierra con los numeros del momento, no con un calculo viejo
    if not p.lines:
        raise InvalidTransition("La planilla no tiene empleados.")
    p.status = PayrollStatus.CLOSED
    p.closed_at = datetime.now(timezone.utc)
    p.closed_by_user_id = user_id
    return p


def delete_draft(db: Session, payroll_id) -> None:
    db.delete(_lock_draft(db, payroll_id))


def set_override(db: Session, *, employee_id, start: date, end: date, force_bonus: bool, adjustment: Decimal,
                 reason: str | None, user_id) -> HrPayrollOverride:
    _get(db, HrEmployee, employee_id, "Empleado")
    if adjustment != 0 and not reason:
        raise SalesError("Un ajuste de monto necesita motivo.")
    for d in (start, end):
        _guard_closed_period(db, employee_id, d)
    ov = db.execute(select(HrPayrollOverride).where(
        HrPayrollOverride.employee_id == employee_id, HrPayrollOverride.period_start == start,
        HrPayrollOverride.period_end == end).with_for_update()).scalar_one_or_none()
    if ov is None:
        ov = HrPayrollOverride(id=uuid.uuid4(), employee_id=employee_id, period_start=start, period_end=end,
                               created_by_user_id=user_id)
        db.add(ov)
    ov.force_bonus, ov.adjustment, ov.reason = force_bonus, adjustment, reason
    db.flush()
    return ov
