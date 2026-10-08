"""Facturacion interna, cobros y su aplicacion (cuentas por cobrar).

IMPORTANTE: `sales_invoices` es un comprobante INTERNO de gestion. Mientras
`fiscal_status = INTERNAL_SIMULATION` no es una factura legal ni electronica:
la emision tributaria real (SIFEN/DNIT) esta bloqueada hasta validar manual
tecnico, timbrado, certificado y homologacion (ver docs/ERP_DATA_MODEL.md).

Saldos: `balance_due` y `unapplied_amount` se mantienen en la misma
transaccion que la aplicacion, con lock de fila, y la base impide que
queden negativos o por encima del total (no hay sobreaplicacion posible)."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.tenant_models import TenantBase
from app.tenant_models.sales import PaymentCondition


class InvoiceStatus(str, enum.Enum):
    ISSUED = "ISSUED"
    VOIDED = "VOIDED"


class FiscalStatus(str, enum.Enum):
    """Semantica preparada para SIFEN; hoy solo se usa INTERNAL_SIMULATION.
    Ningun codigo puede pasar a PENDING_TRANSMISSION/APPROVED mientras la
    integracion SIFEN no exista y este habilitada."""

    INTERNAL_SIMULATION = "INTERNAL_SIMULATION"
    PENDING_TRANSMISSION = "PENDING_TRANSMISSION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ReceiptStatus(str, enum.Enum):
    POSTED = "POSTED"
    VOIDED = "VOIDED"


class PaymentMethod(str, enum.Enum):
    CASH = "CASH"
    TRANSFER = "TRANSFER"
    CARD = "CARD"
    CHECK = "CHECK"
    OTHER = "OTHER"


class SalesInvoice(TenantBase):
    __tablename__ = "sales_invoices"
    __table_args__ = (
        CheckConstraint("total >= 0", name="ck_sales_invoices_total_non_negative"),
        CheckConstraint("balance_due >= 0 AND balance_due <= total", name="ck_sales_invoices_balance_range"),
        CheckConstraint(
            "taxable_10 + vat_10 + taxable_5 + vat_5 + exempt = total", name="ck_sales_invoices_breakdown_sums"
        ),
        CheckConstraint("due_date >= issue_date", name="ck_sales_invoices_due_after_issue"),
        # Un pedido se factura una sola vez mientras la factura este vigente.
        Index("uq_sales_invoices_active_order", "order_id", unique=True, postgresql_where=text("status = 'ISSUED'")),
        Index("ix_sales_invoices_customer_due", "customer_id", "due_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id"), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus, name="invoice_status"), nullable=False)
    fiscal_status: Mapped[FiscalStatus] = mapped_column(Enum(FiscalStatus, name="fiscal_status"), nullable=False)
    payment_condition: Mapped[PaymentCondition] = mapped_column(
        Enum(PaymentCondition, name="payment_condition", create_type=False), nullable=False
    )
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("currencies.code"), nullable=False)
    taxable_10: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_10: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    taxable_5: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_5: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    exempt: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    balance_due: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerReceipt(TenantBase):
    __tablename__ = "customer_receipts"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_customer_receipts_amount_positive"),
        CheckConstraint("unapplied_amount >= 0 AND unapplied_amount <= amount", name="ck_customer_receipts_unapplied_range"),
        Index("uq_customer_receipts_idempotency", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), nullable=False, index=True)
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod, name="payment_method"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("currencies.code"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # Lo cobrado que no se aplico a ninguna factura: queda como anticipo.
    unapplied_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[ReceiptStatus] = mapped_column(Enum(ReceiptStatus, name="receipt_status"), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    allocations: Mapped[list["ReceiptAllocation"]] = relationship(back_populates="receipt", cascade="all")


class ReceiptAllocation(TenantBase):
    __tablename__ = "receipt_allocations"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_receipt_allocations_amount_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customer_receipts.id"), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_invoices.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    receipt: Mapped[CustomerReceipt] = relationship(back_populates="allocations")
