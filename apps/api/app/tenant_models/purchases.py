"""Compras (Procure-to-Pay): ordenes de compra, recepciones (via
inventario), facturas de proveedor y pagos.

Las facturas de proveedor son documentos DE TERCEROS: aca solo se registran
sus datos (numero, timbrado) para control y cuentas por pagar; no se emite
nada. El costo que entra al inventario es el NETO de IVA (el IVA de compras
es credito fiscal), calculado de la linea de la OC."""

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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.tenant_models import TenantBase
from app.tenant_models.receivables import InvoiceStatus, PaymentMethod, ReceiptStatus


class PurchaseOrderStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class PurchaseOrder(TenantBase):
    __tablename__ = "purchase_orders"
    __table_args__ = (CheckConstraint("total >= 0", name="ck_purchase_orders_total_non_negative"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), nullable=False, index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        Enum(PurchaseOrderStatus, name="purchase_order_status"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("currencies.code"), nullable=False)
    expected_date: Mapped[date | None] = mapped_column(Date)
    subtotal_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    close_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="PurchaseOrderLine.line_no"
    )


class PurchaseOrderLine(TenantBase):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no", name="uq_purchase_order_lines_order_line"),
        CheckConstraint("quantity > 0", name="ck_purchase_order_lines_quantity_positive"),
        CheckConstraint("quantity_received >= 0 AND quantity_received <= quantity", name="ck_purchase_order_lines_received_range"),
        CheckConstraint("unit_price >= 0", name="ck_purchase_order_lines_price_non_negative"),
        CheckConstraint("line_total = line_net + line_tax", name="ck_purchase_order_lines_total_consistent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    quantity_received: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    # Precio unitario IVA INCLUIDO, como figura en la factura del proveedor.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_code: Mapped[str] = mapped_column(String(20), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    line_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    line_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class SupplierInvoice(TenantBase):
    __tablename__ = "supplier_invoices"
    __table_args__ = (
        CheckConstraint("total > 0", name="ck_supplier_invoices_total_positive"),
        CheckConstraint("balance_due >= 0 AND balance_due <= total", name="ck_supplier_invoices_balance_range"),
        CheckConstraint("taxable_10 + vat_10 + taxable_5 + vat_5 + exempt = total", name="ck_supplier_invoices_breakdown_sums"),
        CheckConstraint("due_date >= issue_date", name="ck_supplier_invoices_due_after_issue"),
        # Control de duplicados: la misma factura del mismo proveedor no se
        # carga dos veces mientras este vigente.
        Index("uq_supplier_invoices_number", "supplier_id", "supplier_invoice_number", unique=True,
              postgresql_where=text("status = 'ISSUED'")),
        Index("ix_supplier_invoices_supplier_due", "supplier_id", "due_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), nullable=False)
    supplier_invoice_number: Mapped[str] = mapped_column(String(30), nullable=False)
    supplier_timbrado: Mapped[str | None] = mapped_column(String(20))
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"), index=True)
    status: Mapped[InvoiceStatus] = mapped_column(ENUM(InvoiceStatus, name="invoice_status", create_type=False), nullable=False)
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


class SupplierPayment(TenantBase):
    __tablename__ = "supplier_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_supplier_payments_amount_positive"),
        CheckConstraint("unapplied_amount >= 0 AND unapplied_amount <= amount", name="ck_supplier_payments_unapplied_range"),
        Index("uq_supplier_payments_idempotency", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), nullable=False, index=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(ENUM(PaymentMethod, name="payment_method", create_type=False), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("currencies.code"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    unapplied_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[ReceiptStatus] = mapped_column(ENUM(ReceiptStatus, name="receipt_status", create_type=False), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    allocations: Mapped[list["SupplierPaymentAllocation"]] = relationship(back_populates="payment", cascade="all")


class SupplierPaymentAllocation(TenantBase):
    __tablename__ = "supplier_payment_allocations"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_supplier_payment_allocations_amount_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("supplier_payments.id"), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("supplier_invoices.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    payment: Mapped[SupplierPayment] = relationship(back_populates="allocations")
