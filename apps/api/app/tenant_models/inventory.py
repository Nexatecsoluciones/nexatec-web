"""Inventario: libro de movimientos inmutable + saldos por deposito.

`stock_movements` nunca se actualiza ni se borra: un error se corrige con un
movimiento de reversion (`reverses_movement_id`, unico -> cada movimiento se
revierte a lo sumo una vez). `stock_balances` es el saldo materializado,
actualizado en la MISMA transaccion que el movimiento, con lock de fila.
Las reglas duras (cantidad > 0, saldo >= 0, reservado <= saldo) viven como
CHECK en la base, no solo en la aplicacion."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.tenant_models import TenantBase


class MovementType(str, enum.Enum):
    RECEIPT = "RECEIPT"
    ISSUE = "ISSUE"
    ADJUSTMENT_IN = "ADJUSTMENT_IN"
    ADJUSTMENT_OUT = "ADJUSTMENT_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    REVERSAL = "REVERSAL"


class StockMovement(TenantBase):
    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_stock_movements_quantity_positive"),
        CheckConstraint("direction IN (1, -1)", name="ck_stock_movements_direction"),
        CheckConstraint("unit_cost >= 0", name="ck_stock_movements_unit_cost_non_negative"),
        Index("ix_stock_movements_product_created", "product_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    movement_type: Mapped[MovementType] = mapped_column(Enum(MovementType, name="stock_movement_type"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False, index=True)
    # Cantidad siempre positiva; el sentido lo da `direction` (+1 entra, -1 sale).
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    direction: Mapped[int] = mapped_column(nullable=False)
    # Costo congelado al momento del movimiento (promedio vigente en salidas).
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    # Agrupa las dos patas de una transferencia, o un documento futuro (venta, compra).
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    reference: Mapped[str | None] = mapped_column(String(120))
    reverses_movement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_movements.id"), unique=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(100), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    # Usuario del control plane (otra base): sin FK posible, solo el id.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StockBalance(TenantBase):
    __tablename__ = "stock_balances"
    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="ck_stock_balances_on_hand_non_negative"),
        CheckConstraint("reserved >= 0", name="ck_stock_balances_reserved_non_negative"),
        CheckConstraint("reserved <= on_hand", name="ck_stock_balances_reserved_le_on_hand"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), primary_key=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), primary_key=True)
    on_hand: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
