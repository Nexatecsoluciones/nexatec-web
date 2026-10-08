"""RR.HH.: obras/centros de trabajo, personal, asignaciones, asistencia,
horas extra, adelantos, ausencias y cierres de pago (planillas).

Basado en el modulo de RR.HH. que NEXATEC construyo para obras de
construccion, generalizado: las reglas que alla eran fijas (jornada de 10 h,
sabado medio dia, sin IPS) aca son configuracion de la empresa (HrSettings) y
de cada obra. Sin datos biometricos: la marcacion por dispositivo identifica
al trabajador por C.I. en un celular/tablet habilitado de la obra.

Dinero NUMERIC, horas NUMERIC(6,2) (nunca float). Un cierre CLOSED congela sus
lineas (trigger): corregir = reabrir no existe, se hace un ajuste en el
periodo siguiente, igual que en contabilidad."""

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, SmallInteger, String, Text, Time,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.tenant_models import TenantBase

HOURS = Numeric(6, 2)
MONEY = Numeric(18, 2)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PayPeriod(str, enum.Enum):
    BIWEEKLY = "BIWEEKLY"   # quincenal (1-15 / 16-fin)
    MONTHLY = "MONTHLY"


class PayMethod(str, enum.Enum):
    CASH = "CASH"
    TRANSFER = "TRANSFER"


class AttendanceSource(str, enum.Enum):
    MANUAL = "MANUAL"     # cargada por RR.HH. / supervisor
    DEVICE = "DEVICE"     # celular/tablet habilitado de la obra
    IMPORT = "IMPORT"     # importada de planilla / reloj


class OvertimeStatus(str, enum.Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AbsenceKind(str, enum.Enum):
    VACATION = "VACATION"
    PERMISSION = "PERMISSION"
    MEDICAL = "MEDICAL"
    OTHER = "OTHER"


class PayrollStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CLOSED = "CLOSED"


class HrSettings(TenantBase):
    """Fila unica (id=1) con las reglas de la empresa."""

    __tablename__ = "hr_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_hr_settings_singleton"),
        CheckConstraint("workday_hours > 0 AND workday_hours <= 24", name="ck_hr_settings_workday"),
        CheckConstraint("rounding_minutes IN (1, 5, 10, 15, 30)", name="ck_hr_settings_rounding"),
        CheckConstraint("ips_employee_pct >= 0 AND ips_employee_pct <= 100 AND ips_employer_pct >= 0 AND ips_employer_pct <= 100",
                        name="ck_hr_settings_ips_pct"),
        CheckConstraint("overtime_multiplier >= 1", name="ck_hr_settings_ot_mult"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    pay_period: Mapped[PayPeriod] = mapped_column(Enum(PayPeriod, name="hr_pay_period"), nullable=False, default=PayPeriod.BIWEEKLY)
    # Cuanto "vale" un dia normal, independientemente del reloj de la obra
    # (el horario de la obra solo sirve de referencia para tardanza/extra).
    workday_hours: Mapped[Decimal] = mapped_column(HOURS, nullable=False, default=Decimal("8"))
    rounding_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=15)
    # Recargo de la hora extra aprobada (1.5 = 50 % mas, art. 234 C.T.).
    overtime_multiplier: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False, default=Decimal("1.5"))
    # Premio por hora: solo si no hubo falta injustificada en el periodo.
    attendance_bonus_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deduct_ips: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ips_employee_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("9"))
    ips_employer_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("16.5"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class HrCategory(TenantBase):
    """Categoria (oficial, ayudante, administrativo...) con jornal/hora por
    defecto para autocompletar altas."""

    __tablename__ = "hr_categories"
    __table_args__ = (CheckConstraint("hourly_rate >= 0", name="ck_hr_categories_rate"),)

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    default_trade: Mapped[str | None] = mapped_column(String(100))
    hourly_rate: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    # Sueldo negociado individualmente: no se autocompleta ni se propaga.
    manual_rate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class HrSite(TenantBase):
    """Obra o centro de trabajo, con su horario de referencia."""

    __tablename__ = "hr_sites"
    __table_args__ = (
        CheckConstraint("tolerance_minutes >= 0 AND tolerance_minutes <= 120", name="ck_hr_sites_tolerance"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_hr_sites_geo_pair"),
        CheckConstraint("geofence_radius_m BETWEEN 20 AND 5000", name="ck_hr_sites_radius"),
    )

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(160))
    location: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(7, 0))
    end_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(15, 0))
    # 0=lunes ... 6=domingo
    workdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), nullable=False, default=lambda: [0, 1, 2, 3, 4])
    tolerance_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=10)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    geofence_radius_m: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    created_at: Mapped[datetime] = _created()

    day_schedules: Mapped[list["HrSiteDaySchedule"]] = relationship(
        back_populates="site", cascade="all, delete-orphan", order_by="HrSiteDaySchedule.weekday")


class HrSiteDaySchedule(TenantBase):
    """Excepcion para un dia de la semana (ej. sabado 07:00-12:00): ese dia
    vale lo que dura la excepcion, no la jornada completa."""

    __tablename__ = "hr_site_day_schedules"
    __table_args__ = (
        UniqueConstraint("site_id", "weekday", name="uq_hr_site_day"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_hr_site_day_weekday"),
        CheckConstraint("end_time > start_time", name="ck_hr_site_day_range"),
    )

    id: Mapped[uuid.UUID] = _pk()
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_sites.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    site: Mapped[HrSite] = relationship(back_populates="day_schedules")


class HrEmployee(TenantBase):
    __tablename__ = "hr_employees"
    __table_args__ = (
        UniqueConstraint("national_id", name="uq_hr_employees_ci"),
        CheckConstraint("hourly_rate >= 0 AND bonus_per_hour >= 0", name="ck_hr_employees_rates"),
        CheckConstraint("ips_exit IS NULL OR ips_entry IS NULL OR ips_exit >= ips_entry", name="ck_hr_employees_ips_range"),
        CheckConstraint("(custom_start IS NULL) = (custom_end IS NULL)", name="ck_hr_employees_custom_pair"),
    )

    id: Mapped[uuid.UUID] = _pk()
    national_id: Mapped[str] = mapped_column(String(20), nullable=False)   # C.I.
    last_names: Mapped[str] = mapped_column(String(120), nullable=False)
    first_names: Mapped[str] = mapped_column(String(120), nullable=False)
    trade: Mapped[str | None] = mapped_column(String(100))                # oficio
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_categories.id"))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(150))
    pay_method: Mapped[PayMethod] = mapped_column(Enum(PayMethod, name="hr_pay_method"), nullable=False, default=PayMethod.CASH)
    bank_account: Mapped[str | None] = mapped_column(String(60))
    hourly_rate: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    bonus_per_hour: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    ips_entry: Mapped[date | None] = mapped_column(Date)
    ips_exit: Mapped[date | None] = mapped_column(Date)
    ips_notes: Mapped[str | None] = mapped_column(Text)
    # Ficha de personal
    address: Mapped[str | None] = mapped_column(String(200))
    neighborhood: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    birth_date: Mapped[date | None] = mapped_column(Date)
    family_notes: Mapped[str | None] = mapped_column(Text)
    training: Mapped[str | None] = mapped_column(Text)
    skills: Mapped[str | None] = mapped_column(Text)
    references_notes: Mapped[str | None] = mapped_column(Text)
    # Horario individual: reemplaza al de la obra como referencia de
    # tardanza/extra (no cambia cuanto vale el dia). No aplica en dias con
    # excepcion de la obra (ej. sabado medio dia).
    custom_start: Mapped[time | None] = mapped_column(Time)
    custom_end: Mapped[time | None] = mapped_column(Time)
    tracks_attendance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    @property
    def full_name(self) -> str:
        return f"{self.last_names}, {self.first_names}"


class HrAssignment(TenantBase):
    """Historial de en que obra estuvo cada uno. Una sola asignacion abierta
    (end_date NULL) por empleado (indice parcial unico en la migracion)."""

    __tablename__ = "hr_assignments"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_hr_assignments_range"),
        Index("uq_hr_assignments_open", "employee_id", unique=True, postgresql_where=text("end_date IS NULL")),
    )

    id: Mapped[uuid.UUID] = _pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False, index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_sites.id"), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(250))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class HrDevice(TenantBase):
    """Celular/tablet de una obra habilitado para marcar asistencia por un
    link sin login. Se guarda solo el HASH del token. Queda atado al primer
    aparato que lo abre (identificador propio en localStorage)."""

    __tablename__ = "hr_devices"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_sites.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    bound_fingerprint: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class HrAttendance(TenantBase):
    """Un registro por empleado, obra y dia."""

    __tablename__ = "hr_attendance"
    __table_args__ = (
        UniqueConstraint("employee_id", "site_id", "work_date", name="uq_hr_attendance_day"),
        CheckConstraint("regular_hours >= 0 AND overtime_hours >= 0 AND paid_hours >= 0", name="ck_hr_attendance_hours"),
        CheckConstraint("(overtime_hours = 0) = (overtime_status = 'NONE')", name="ck_hr_attendance_ot_status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False, index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_sites.id"), nullable=False, index=True)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    time_in: Mapped[time | None] = mapped_column(Time)
    time_out: Mapped[time | None] = mapped_column(Time)
    regular_hours: Mapped[Decimal] = mapped_column(HOURS, nullable=False, default=Decimal("0"))
    overtime_hours: Mapped[Decimal] = mapped_column(HOURS, nullable=False, default=Decimal("0"))
    overtime_status: Mapped[OvertimeStatus] = mapped_column(Enum(OvertimeStatus, name="hr_overtime_status"), nullable=False, default=OvertimeStatus.NONE)
    # Lo que se paga: normales + extra aprobada (o lo forzado a mano).
    paid_hours: Mapped[Decimal] = mapped_column(HOURS, nullable=False, default=Decimal("0"))
    manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[AttendanceSource] = mapped_column(Enum(AttendanceSource, name="hr_attendance_source"), nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_devices.id"))
    # Idempotencia de envios desde el celular (reintentos sin señal).
    in_client_id: Mapped[str | None] = mapped_column(String(80))
    out_client_id: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(String(250))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class HrManualOvertime(TenantBase):
    """Horas extra sin marcacion (cargadas por RR.HH.), ya aprobadas."""

    __tablename__ = "hr_manual_overtime"
    __table_args__ = (CheckConstraint("hours > 0 AND hours <= 24", name="ck_hr_manual_overtime_hours"),)

    id: Mapped[uuid.UUID] = _pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False, index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_sites.id"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(HOURS, nullable=False)
    reason: Mapped[str] = mapped_column(String(250), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class HrAdvance(TenantBase):
    """Adelanto (anticipo) de sueldo: se descuenta en el cierre del periodo."""

    __tablename__ = "hr_advances"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_hr_advances_amount"),)

    id: Mapped[uuid.UUID] = _pk()
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False, index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_sites.id"))
    advance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    pay_method: Mapped[PayMethod] = mapped_column(Enum(PayMethod, name="hr_pay_method"), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(250))
    voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    void_reason: Mapped[str | None] = mapped_column(String(250))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class HrAbsence(TenantBase):
    """Ausencia justificada (no cuenta como falta para el premio)."""

    __tablename__ = "hr_absences"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="ck_hr_absences_range"),)

    id: Mapped[uuid.UUID] = _pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[AbsenceKind] = mapped_column(Enum(AbsenceKind, name="hr_absence_kind"), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(250))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class HrBlocklist(TenantBase):
    """Personas que no se pueden contratar/reactivar (por C.I.). Cada fila es
    un incidente."""

    __tablename__ = "hr_blocklist"

    id: Mapped[uuid.UUID] = _pk()
    national_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(180))
    reason: Mapped[str] = mapped_column(String(250), nullable=False)
    included_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class HrPayroll(TenantBase):
    """Cierre de pago de un periodo (todas las obras o una). DRAFT se
    recalcula; CLOSED queda congelado con sus lineas."""

    __tablename__ = "hr_payrolls"
    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="ck_hr_payrolls_range"),
        CheckConstraint("(status = 'CLOSED') = (closed_at IS NOT NULL)", name="ck_hr_payrolls_closed_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_sites.id"))
    status: Mapped[PayrollStatus] = mapped_column(Enum(PayrollStatus, name="hr_payroll_status"), nullable=False, default=PayrollStatus.DRAFT)
    total_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    total_gross: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    total_ips_employee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    total_ips_employer: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    total_advances: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    total_net: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()

    lines: Mapped[list["HrPayrollLine"]] = relationship(
        back_populates="payroll", cascade="all, delete-orphan", order_by="HrPayrollLine.employee_name")


class HrPayrollLine(TenantBase):
    """Fila de la planilla, en cascada: horas -> monto -> extra -> premio ->
    bruto -> IPS -> adelantos -> neto. Snapshot de nombre/C.I./tarifas al
    momento del calculo (el recibo no cambia si despues se edita la ficha)."""

    __tablename__ = "hr_payroll_lines"
    __table_args__ = (
        UniqueConstraint("payroll_id", "employee_id", name="uq_hr_payroll_lines_employee"),
        CheckConstraint("gross = base_amount + overtime_amount + bonus_amount + adjustment", name="ck_hr_payroll_lines_gross"),
        CheckConstraint("net = gross - ips_employee - advances", name="ck_hr_payroll_lines_net"),
    )

    id: Mapped[uuid.UUID] = _pk()
    payroll_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_payrolls.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False)
    employee_name: Mapped[str] = mapped_column(String(250), nullable=False)
    national_id: Mapped[str] = mapped_column(String(20), nullable=False)
    pay_method: Mapped[PayMethod] = mapped_column(Enum(PayMethod, name="hr_pay_method"), nullable=False)
    bank_account: Mapped[str | None] = mapped_column(String(60))
    hourly_rate: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    regular_hours: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    overtime_hours: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    days_worked: Mapped[int] = mapped_column(Integer, nullable=False)
    unexcused_absences: Mapped[int] = mapped_column(Integer, nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    overtime_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    bonus_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    adjustment: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    gross: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    ips_employee: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    ips_employer: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    advances: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    bonus_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    adjustment_note: Mapped[str | None] = mapped_column(String(250))

    payroll: Mapped[HrPayroll] = relationship(back_populates="lines")


class HrPayrollOverride(TenantBase):
    """Excepciones manuales para un periodo: forzar el premio aunque haya
    faltado, o ajustar el bruto (+/-) con motivo. Se aplican al calcular."""

    __tablename__ = "hr_payroll_overrides"
    __table_args__ = (
        UniqueConstraint("employee_id", "period_start", "period_end", name="uq_hr_payroll_overrides_period"),
        CheckConstraint("adjustment = 0 OR reason IS NOT NULL", name="ck_hr_payroll_overrides_reason"),
    )

    id: Mapped[uuid.UUID] = _pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    force_bonus: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    adjustment: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    reason: Mapped[str | None] = mapped_column(String(250))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()
