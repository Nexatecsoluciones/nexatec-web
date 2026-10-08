"""Pedidos de venta: DRAFT -> CONFIRMED (reserva stock) -> DELIVERED (descuenta
stock, congela costo) | CANCELLED (libera reserva).

IVA: los precios son IVA INCLUIDO. Por linea:
    bruto = cantidad * precio * (1 - descuento%)      -> redondeado a la moneda
    iva   = bruto * tasa / (100 + tasa)               -> redondeado a la moneda
    neto  = bruto - iva
La tasa vigente se congela en la linea. Esto es calculo de GESTION; la
liquidacion tributaria la valida un contador y, para comprobantes
electronicos, SIFEN (no implementado).

Nada hace commit aca: una request = una transaccion (ver routers)."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.services import inventory
from app.tenant_models.core import Company, Currency, Party, Product, ProductType, Tax, Warehouse
from app.tenant_models.sales import (
    DocumentSequence,
    PaymentCondition,
    SalesOrder,
    SalesOrderLine,
    SalesOrderStatus,
)


class SalesError(Exception):
    status_code = 422

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidTransition(SalesError):
    status_code = 409


class NotFound(SalesError):
    status_code = 404


@dataclass
class LineInput:
    product_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal | None = None
    discount_pct: Decimal = Decimal("0")
    description: str | None = None


def _q(decimals: int) -> Decimal:
    return Decimal(1).scaleb(-decimals)


def compute_line(quantity: Decimal, unit_price: Decimal, discount_pct: Decimal, tax_rate: Decimal,
                 decimals: int) -> tuple[Decimal, Decimal, Decimal]:
    q = _q(decimals)
    gross = (quantity * unit_price * (Decimal(100) - discount_pct) / Decimal(100)).quantize(q, rounding=ROUND_HALF_UP)
    tax = (gross * tax_rate / (Decimal(100) + tax_rate)).quantize(q, rounding=ROUND_HALF_UP)
    return gross - tax, tax, gross


def next_number(db: Session, code: str) -> str:
    seq = db.execute(select(DocumentSequence).where(DocumentSequence.code == code).with_for_update()).scalar_one()
    value = seq.next_value
    seq.next_value = value + 1
    return f"{seq.prefix}{value:06d}"


def _current_tax_rate(db: Session, code: str) -> Decimal:
    today = func.current_date()
    rate = db.execute(
        select(Tax.rate)
        .where(Tax.code == code, Tax.valid_from <= today, or_(Tax.valid_to.is_(None), Tax.valid_to >= today))
        .order_by(Tax.valid_from.desc())
        .limit(1)
    ).scalar_one_or_none()
    if rate is None:
        raise SalesError(f"No hay tasa vigente para el impuesto {code}.")
    return rate


def _currency(db: Session) -> Currency:
    company = db.get(Company, 1)
    return db.get(Currency, company.base_currency if company else "PYG")


def _build_lines(db: Session, order: SalesOrder, lines: list[LineInput], decimals: int) -> None:
    if not lines:
        raise SalesError("El pedido tiene que tener al menos una linea.")
    order.lines.clear()
    db.flush()
    net = tax = total = Decimal(0)
    for i, li in enumerate(lines, start=1):
        product = db.get(Product, li.product_id)
        if product is None or not product.is_active:
            raise SalesError("Producto inexistente o inactivo.")
        if li.quantity <= 0:
            raise SalesError("La cantidad tiene que ser mayor a cero.")
        price = product.sale_price if li.unit_price is None else li.unit_price
        rate = _current_tax_rate(db, product.tax_code)
        l_net, l_tax, l_total = compute_line(li.quantity, price, li.discount_pct, rate, decimals)
        order.lines.append(SalesOrderLine(
            line_no=i, product_id=product.id, description=li.description or product.name,
            quantity=li.quantity, unit_price=price, discount_pct=li.discount_pct,
            tax_code=product.tax_code, tax_rate=rate, line_net=l_net, line_tax=l_tax, line_total=l_total,
        ))
        net += l_net
        tax += l_tax
        total += l_total
    order.subtotal_net, order.tax_total, order.total = net, tax, total


def create_order(db: Session, *, user_id: uuid.UUID | None, customer_id: uuid.UUID, warehouse_id: uuid.UUID,
                 payment_condition: PaymentCondition, lines: list[LineInput], notes: str | None) -> SalesOrder:
    customer = db.get(Party, customer_id)
    if customer is None or not customer.is_customer or not customer.is_active:
        raise SalesError("El cliente no existe, no es cliente o esta inactivo.")
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None or not warehouse.is_active:
        raise SalesError("Deposito inexistente o inactivo.")
    currency = _currency(db)

    order = SalesOrder(
        id=uuid.uuid4(), number=next_number(db, "SALES_ORDER"), customer_id=customer.id,
        warehouse_id=warehouse.id, status=SalesOrderStatus.DRAFT, payment_condition=payment_condition,
        currency=currency.code, notes=notes, created_by_user_id=user_id,
    )
    db.add(order)
    _build_lines(db, order, lines, currency.decimals)
    return order


def _lock_order(db: Session, order_id: uuid.UUID) -> SalesOrder:
    order = db.execute(
        select(SalesOrder).where(SalesOrder.id == order_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if order is None:
        raise NotFound("Pedido no encontrado.")
    return order


def _stock_lines(db: Session, order: SalesOrder) -> list[SalesOrderLine]:
    """Lineas de bienes con stock, ordenadas por producto (orden fijo de
    locks entre pedidos concurrentes)."""
    result = []
    for line in order.lines:
        product = db.get(Product, line.product_id)
        if product.product_type == ProductType.GOOD and product.tracks_stock:
            result.append(line)
    return sorted(result, key=lambda ln: (str(ln.product_id), ln.line_no))


def replace_lines(db: Session, order_id: uuid.UUID, lines: list[LineInput]) -> SalesOrder:
    order = _lock_order(db, order_id)
    if order.status != SalesOrderStatus.DRAFT:
        raise InvalidTransition("Solo se pueden editar pedidos en borrador.")
    _build_lines(db, order, lines, _currency(db).decimals)
    return order


def confirm(db: Session, order_id: uuid.UUID, user_id: uuid.UUID | None) -> SalesOrder:
    order = _lock_order(db, order_id)
    if order.status == SalesOrderStatus.CONFIRMED:
        return order
    if order.status != SalesOrderStatus.DRAFT:
        raise InvalidTransition(f"No se puede confirmar un pedido {order.status.value}.")
    op = inventory.OpContext(db=db, user_id=user_id, reference=order.number)
    for line in _stock_lines(db, order):
        inventory.reserve(op, line.product_id, order.warehouse_id, line.quantity)
    order.status = SalesOrderStatus.CONFIRMED
    order.confirmed_at = datetime.now(timezone.utc)
    return order


def deliver(db: Session, order_id: uuid.UUID, user_id: uuid.UUID | None) -> SalesOrder:
    order = _lock_order(db, order_id)
    if order.status == SalesOrderStatus.DELIVERED:
        return order  # reintento: no vuelve a descontar
    if order.status != SalesOrderStatus.CONFIRMED:
        raise InvalidTransition(f"No se puede entregar un pedido {order.status.value}.")
    op = inventory.OpContext(db=db, user_id=user_id, reference=order.number)
    for line in _stock_lines(db, order):
        movement = inventory.issue_reserved(op, line.product_id, order.warehouse_id, line.quantity, group_id=order.id)
        line.unit_cost = movement.unit_cost
    order.status = SalesOrderStatus.DELIVERED
    order.delivered_at = datetime.now(timezone.utc)
    return order


def cancel(db: Session, order_id: uuid.UUID, user_id: uuid.UUID | None, reason: str) -> SalesOrder:
    order = _lock_order(db, order_id)
    if order.status == SalesOrderStatus.CANCELLED:
        return order
    if order.status == SalesOrderStatus.DELIVERED:
        raise InvalidTransition("Un pedido entregado no se cancela: se hace una devolucion/nota de credito.")
    if order.status == SalesOrderStatus.CONFIRMED:
        op = inventory.OpContext(db=db, user_id=user_id, reference=order.number)
        for line in _stock_lines(db, order):
            inventory.release(op, line.product_id, order.warehouse_id, line.quantity)
    order.status = SalesOrderStatus.CANCELLED
    order.cancelled_at = datetime.now(timezone.utc)
    order.cancel_reason = reason
    return order
