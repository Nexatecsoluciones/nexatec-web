"""Venta rapida (POS), caja y codigos de barras.

No es un motor de ventas paralelo: una venta rapida es un pedido de venta +
entrega + factura interna + cobros de los modulos existentes (app/services
sales, receivables, accounting), ejecutados en UNA transaccion. Aca solo se
guarda lo propio del mostrador: que caja/turno la cobro, los medios de pago,
el vuelto y la anulacion.

Caja: un turno (cash_session) por caja y por cajero a la vez. Lo esperado en
efectivo = fondo inicial + suma firmada de cash_movements en efectivo. Los
movimientos son inmutables (trigger): una anulacion agrega un movimiento
compensatorio, nunca borra."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Numeric, SmallInteger, String, Text, func, text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.tenant_models import TenantBase
from app.tenant_models.receivables import PaymentMethod

MONEY = Numeric(18, 2)


class Symbology(str, enum.Enum):
    EAN13 = "EAN13"
    UPCA = "UPCA"
    EAN8 = "EAN8"
    CODE128 = "CODE128"


class SessionStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CashMovementKind(str, enum.Enum):
    SALE = "SALE"                 # cobro de una venta rapida
    COLLECTION = "COLLECTION"     # cobro de saldo de un cliente en caja
    VOID = "VOID"                 # reverso por anulacion de venta/cobro
    CASH_IN = "CASH_IN"           # otros ingresos de efectivo
    EXPENSE = "EXPENSE"           # gasto pagado con la caja
    TO_BANK = "TO_BANK"           # retiro/deposito al banco
    FROM_BANK = "FROM_BANK"       # cambio traido del banco


class PosSaleStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    VOIDED = "VOIDED"


class ProductBarcode(TenantBase):
    """Varios codigos por producto, uno principal. Unico por empresa. Los
    EAN/UPC se validan por digito verificador (son del fabricante); los
    internos son CODE128 con prefijo propio y nunca imitan un GTIN de GS1."""

    __tablename__ = "product_barcodes"
    __table_args__ = (
        Index("uq_product_barcodes_code", "code", unique=True),
        Index("uq_product_barcodes_primary", "product_id", unique=True, postgresql_where=text("is_primary")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    symbology: Mapped[Symbology] = mapped_column(Enum(Symbology, name="barcode_symbology"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CashRegister(TenantBase):
    """Caja fisica (mostrador). Vende desde un deposito."""

    __tablename__ = "cash_registers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    default_customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CashSession(TenantBase):
    """Turno de caja: apertura con fondo, cierre con arqueo."""

    __tablename__ = "cash_sessions"
    __table_args__ = (
        Index("uq_cash_sessions_open_register", "register_id", unique=True, postgresql_where=text("status = 'OPEN'")),
        Index("uq_cash_sessions_open_user", "opened_by_user_id", unique=True, postgresql_where=text("status = 'OPEN'")),
        CheckConstraint("opening_float >= 0", name="ck_cash_sessions_float"),
        CheckConstraint("(status = 'CLOSED') = (closed_at IS NOT NULL)", name="ck_cash_sessions_closed_at"),
        CheckConstraint("status = 'OPEN' OR (counted_cash IS NOT NULL AND expected_cash IS NOT NULL "
                        "AND difference = counted_cash - expected_cash)", name="ck_cash_sessions_arqueo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    register_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cash_registers.id"), nullable=False, index=True)
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus, name="cash_session_status"), nullable=False)
    opened_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    opening_float: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_cash: Mapped[Decimal | None] = mapped_column(MONEY)
    counted_cash: Mapped[Decimal | None] = mapped_column(MONEY)
    difference: Mapped[Decimal | None] = mapped_column(MONEY)
    # Detalle opcional del conteo: {"100000": 3, "50000": 2, ...}
    count_detail: Mapped[dict | None] = mapped_column(JSONB)
    closing_notes: Mapped[str | None] = mapped_column(Text)


class CashMovement(TenantBase):
    __tablename__ = "cash_movements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_cash_movements_amount"),
        CheckConstraint("direction IN (-1, 1)", name="ck_cash_movements_direction"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cash_sessions.id"), nullable=False, index=True)
    kind: Mapped[CashMovementKind] = mapped_column(Enum(CashMovementKind, name="cash_movement_kind"), nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(ENUM(PaymentMethod, name="payment_method", create_type=False), nullable=False)
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customer_receipts.id"))
    pos_sale_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pos_sales.id"))
    description: Mapped[str | None] = mapped_column(String(250))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PosSale(TenantBase):
    __tablename__ = "pos_sales"
    __table_args__ = (
        Index("uq_pos_sales_idempotency", "idempotency_key", unique=True),
        CheckConstraint("change_given >= 0", name="ck_pos_sales_change"),
        CheckConstraint("(status = 'VOIDED') = (voided_at IS NOT NULL)", name="ck_pos_sales_voided_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cash_sessions.id"), nullable=False, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id"), nullable=False, unique=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_invoices.id"), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), nullable=False)
    status: Mapped[PosSaleStatus] = mapped_column(Enum(PosSaleStatus, name="pos_sale_status"), nullable=False)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    paid: Mapped[Decimal] = mapped_column(MONEY, nullable=False)          # aplicado a la factura
    tendered_cash: Mapped[Decimal] = mapped_column(MONEY, nullable=False)  # efectivo recibido
    change_given: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payments: Mapped[list] = mapped_column(JSONB, nullable=False)          # [{method, amount, reference}]
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    void_reason: Mapped[str | None] = mapped_column(String(250))
    credit_note_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_credit_notes.id"))
