"""Ventas: pedido de venta con lineas, y numeracion correlativa de documentos.

Los montos y la tasa de IVA de cada linea quedan CONGELADOS al crear/editar
el pedido; el costo unitario se congela al entregar (base del margen). Un
cambio posterior de precio de lista, costo o tasa nunca altera un pedido
existente."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
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


class DocumentSequence(TenantBase):
    """Correlativos sin huecos: se toma con SELECT ... FOR UPDATE dentro de
    la misma transaccion que crea el documento (si la transaccion falla, el
    numero no se consume)."""

    __tablename__ = "document_sequences"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SalesOrderStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class PaymentCondition(str, enum.Enum):
    CASH = "CASH"
    CREDIT = "CREDIT"


class SalesOrder(TenantBase):
    __tablename__ = "sales_orders"
    __table_args__ = (
        CheckConstraint("total >= 0 AND subtotal_net >= 0 AND tax_total >= 0", name="ck_sales_orders_amounts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), nullable=False, index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    status: Mapped[SalesOrderStatus] = mapped_column(
        Enum(SalesOrderStatus, name="sales_order_status"), nullable=False, default=SalesOrderStatus.DRAFT, index=True
    )
    payment_condition: Mapped[PaymentCondition] = mapped_column(Enum(PaymentCondition, name="payment_condition"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("currencies.code"), nullable=False)
    subtotal_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["SalesOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="SalesOrderLine.line_no"
    )


class SalesOrderLine(TenantBase):
    __tablename__ = "sales_order_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no", name="uq_sales_order_lines_order_line"),
        CheckConstraint("quantity > 0", name="ck_sales_order_lines_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_sales_order_lines_price_non_negative"),
        CheckConstraint("discount_pct >= 0 AND discount_pct <= 100", name="ck_sales_order_lines_discount_range"),
        CheckConstraint("line_total = line_net + line_tax", name="ck_sales_order_lines_total_consistent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    # Precio unitario IVA INCLUIDO (convencion usual en Paraguay).
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0"))
    tax_code: Mapped[str] = mapped_column(String(20), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    line_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    line_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # Costo unitario congelado al entregar (NULL hasta entonces o si es servicio).
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    order: Mapped[SalesOrder] = relationship(back_populates="lines")
