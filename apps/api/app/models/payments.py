import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.payments_enums import (
    BillingPeriod,
    Currency,
    PaymentMethod,
    PaymentOrderStatus,
    SubscriptionStatus,
)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Plan(Base):
    """Definido por admin, nunca por el cliente. El monto que se cobra
    SIEMPRE se recalcula server-side a partir de esta tabla -- ver
    app/routers/payments.py -- el frontend solo puede enviar un plan_id."""

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # NUMERIC, nunca float: la precision de centavos/guaranies no puede
    # depender de la representacion binaria de punto flotante.
    price_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    price_currency: Mapped[Currency] = mapped_column(Enum(Currency, name="plan_currency"), nullable=False)
    billing_period: Mapped[BillingPeriod] = mapped_column(Enum(BillingPeriod, name="billing_period"), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"), nullable=False, default=SubscriptionStatus.TRIAL
    )
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaymentOrder(Base):
    """Una orden/intento de pago. `shop_process_id` es la referencia que
    Bancard exige (entero, definido por el comercio) y que ademas usamos
    como clave de idempotencia del webhook -- Bancard no expone un
    event_id propio (confirmado en su documentacion oficial vPOS 0.3.1),
    asi que la idempotencia se resuelve chequeando el estado actual de
    esta fila antes de mutarla, no con una tabla de eventos separada."""

    __tablename__ = "payment_orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("subscriptions.id"))
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)

    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod, name="payment_method"), nullable=False)
    status: Mapped[PaymentOrderStatus] = mapped_column(
        Enum(PaymentOrderStatus, name="payment_order_status"), nullable=False
    )

    # Recalculado server-side desde Plan en el momento de crear la orden
    # (no se recalcula despues: si el precio del plan cambia, las ordenes
    # ya creadas conservan el monto con el que se le cobro al cliente).
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[Currency] = mapped_column(Enum(Currency, name="payment_currency"), nullable=False)

    # Bancard: identificador propio, unico, numerico (ver docs/PAYMENTS.md).
    shop_process_id: Mapped[int | None] = mapped_column(unique=True)
    bancard_process_id: Mapped[str | None] = mapped_column(String(80))
    bancard_authorization_number: Mapped[str | None] = mapped_column(String(20))
    bancard_response_code: Mapped[str | None] = mapped_column(String(10))

    # Transferencia bancaria
    transfer_proof_media_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id"))
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
