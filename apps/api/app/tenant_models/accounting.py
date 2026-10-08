"""Contabilidad: plan de cuentas, mapeo de cuentas por concepto, periodos y
asientos de doble partida.

Garantias en la base (ver migracion): cada asiento suma debe = haber
(trigger diferido al COMMIT), asientos y lineas son inmutables (trigger),
y cada linea tiene exactamente un lado > 0 (CHECK). Un error se corrige con
un asiento de reversion (`reverses_entry_id`, unico)."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.tenant_models import TenantBase


class AccountType(str, enum.Enum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class PeriodStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Account(TenantBase):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(Enum(AccountType, name="account_type"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    # Solo las cuentas imputables reciben movimientos; las de agrupacion no.
    is_postable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AccountMapping(TenantBase):
    """Que cuenta usa cada concepto de los asientos automaticos (AR, AP,
    IVA, inventario, costo de ventas...). Configurable por empresa."""

    __tablename__ = "account_mappings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)


class FiscalPeriod(TenantBase):
    __tablename__ = "fiscal_periods"
    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_fiscal_periods_year_month"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_fiscal_periods_month_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(Enum(PeriodStatus, name="period_status"), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)


class JournalEntry(TenantBase):
    __tablename__ = "journal_entries"
    __table_args__ = (Index("ix_journal_entries_source", "source_type", "source_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("fiscal_periods.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    # Origen del asiento (MANUAL o el documento que lo genero).
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_entries.id"), unique=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lines: Mapped[list["JournalLine"]] = relationship(back_populates="entry", order_by="JournalLine.line_no")


class JournalLine(TenantBase):
    __tablename__ = "journal_lines"
    __table_args__ = (
        UniqueConstraint("entry_id", "line_no", name="uq_journal_lines_entry_line"),
        CheckConstraint("debit >= 0 AND credit >= 0", name="ck_journal_lines_non_negative"),
        CheckConstraint("(debit > 0) <> (credit > 0)", name="ck_journal_lines_one_side"),
        Index("ix_journal_lines_account", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("journal_entries.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    party_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"))
    description: Mapped[str | None] = mapped_column(String(300))

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
