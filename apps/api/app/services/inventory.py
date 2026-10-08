"""Operaciones de inventario sobre la base de un tenant.

Concurrencia: toda operacion toma primero el lock de la fila del PRODUCTO
(SELECT ... FOR UPDATE) y despues los saldos que toca, ordenados por
warehouse_id. Con ese orden fijo dos operaciones simultaneas sobre el mismo
producto se serializan y no pueden producir deadlock ni dejar stock
negativo (que ademas la base rechaza por CHECK).

Costeo: promedio ponderado a nivel producto (todas las bodegas). Entradas
recalculan el promedio; salidas y transferencias salen al promedio vigente,
que queda congelado en el movimiento.

Idempotencia: si la request trae Idempotency-Key y ya existen movimientos
con esa clave para ese producto, se devuelven esos (no se duplica). El
chequeo se hace DESPUES de tomar el lock del producto, asi dos reintentos
simultaneos tampoco duplican.

Ninguna funcion hace commit: el caller decide (una request = una
transaccion)."""

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.tenant_models.core import Product, Warehouse
from app.tenant_models.inventory import MovementType, StockBalance, StockMovement

QTY_Q = Decimal("0.0001")
COST_Q = Decimal("0.0001")

_OUTBOUND = {MovementType.ISSUE, MovementType.ADJUSTMENT_OUT, MovementType.TRANSFER_OUT}


class InventoryError(Exception):
    status_code = 422

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InsufficientStock(InventoryError):
    status_code = 409


class AlreadyReversed(InventoryError):
    status_code = 409


class NotFound(InventoryError):
    status_code = 404


@dataclass
class OpContext:
    db: Session
    user_id: uuid.UUID | None
    idempotency_key: str | None = None
    reference: str | None = None
    notes: str | None = None
    # Que documento origina el movimiento (define la contrapartida contable):
    # "SALES_ORDER" -> costo de ventas, "PURCHASE_ORDER" -> mercaderias a facturar.
    source: str | None = None


def _qty(value: Decimal) -> Decimal:
    q = Decimal(value).quantize(QTY_Q, rounding=ROUND_HALF_UP)
    if q <= 0:
        raise InventoryError("La cantidad tiene que ser mayor a cero.")
    return q


def _lock_product(db: Session, product_id: uuid.UUID) -> Product:
    # populate_existing: si el objeto ya estaba en la sesion, se pisa con la
    # version recien bloqueada en vez de usar la copia vieja en memoria.
    product = db.execute(
        select(Product).where(Product.id == product_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if product is None:
        raise NotFound("Producto no encontrado.")
    return product


def _check_stockable(product: Product) -> None:
    if not product.tracks_stock:
        raise InventoryError("El producto no maneja stock.")
    if not product.is_active:
        raise InventoryError("El producto esta inactivo.")


def _check_warehouse(db: Session, warehouse_id: uuid.UUID) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise NotFound("Deposito no encontrado.")
    if not warehouse.is_active:
        raise InventoryError("El deposito esta inactivo.")
    return warehouse


def _lock_balance(db: Session, product_id: uuid.UUID, warehouse_id: uuid.UUID) -> StockBalance:
    db.execute(
        insert(StockBalance)
        .values(product_id=product_id, warehouse_id=warehouse_id, on_hand=0, reserved=0)
        .on_conflict_do_nothing()
    )
    return db.execute(
        select(StockBalance)
        .where(StockBalance.product_id == product_id, StockBalance.warehouse_id == warehouse_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def _total_on_hand(db: Session, product_id: uuid.UUID) -> Decimal:
    return db.execute(
        select(func.coalesce(func.sum(StockBalance.on_hand), 0)).where(StockBalance.product_id == product_id)
    ).scalar_one()


def _existing(op: OpContext, product_id: uuid.UUID) -> list[StockMovement] | None:
    if not op.idempotency_key:
        return None
    rows = op.db.execute(
        select(StockMovement)
        .where(StockMovement.idempotency_key == op.idempotency_key, StockMovement.product_id == product_id)
        .order_by(StockMovement.created_at, StockMovement.direction)
    ).scalars().all()
    return list(rows) or None


def _new_average(total: Decimal, avg: Decimal, qty: Decimal, unit_cost: Decimal) -> Decimal:
    new_total = total + qty
    if new_total <= 0:
        return avg
    return ((total * avg + qty * unit_cost) / new_total).quantize(COST_Q, rounding=ROUND_HALF_UP)


def _movement(op: OpContext, *, mtype: MovementType, product: Product, warehouse_id: uuid.UUID,
              qty: Decimal, direction: int, unit_cost: Decimal, group_id: uuid.UUID | None = None,
              reverses: uuid.UUID | None = None) -> StockMovement:
    m = StockMovement(
        id=uuid.uuid4(), movement_type=mtype, product_id=product.id, warehouse_id=warehouse_id,
        quantity=qty, direction=direction, unit_cost=unit_cost.quantize(COST_Q, rounding=ROUND_HALF_UP),
        group_id=group_id, reference=op.reference, reverses_movement_id=reverses,
        idempotency_key=op.idempotency_key, notes=op.notes, created_by_user_id=op.user_id,
    )
    op.db.add(m)
    # Asiento automatico en la MISMA transaccion: si no se puede registrar
    # (periodo cerrado, cuenta sin mapear) el movimiento tampoco ocurre.
    from app.services import accounting

    accounting.post_stock_movement(op.db, m, op.source, op.user_id)
    return m


def _inbound(op: OpContext, mtype: MovementType, product_id: uuid.UUID, warehouse_id: uuid.UUID,
             quantity: Decimal, unit_cost: Decimal | None) -> list[StockMovement]:
    product = _lock_product(op.db, product_id)
    if (prev := _existing(op, product_id)) is not None:
        return prev
    _check_stockable(product)
    _check_warehouse(op.db, warehouse_id)
    qty = _qty(quantity)
    cost = product.average_cost if unit_cost is None else Decimal(unit_cost)
    if cost < 0:
        raise InventoryError("El costo no puede ser negativo.")

    total = _total_on_hand(op.db, product.id)
    balance = _lock_balance(op.db, product.id, warehouse_id)
    product.average_cost = _new_average(total, product.average_cost, qty, cost)
    balance.on_hand += qty
    return [_movement(op, mtype=mtype, product=product, warehouse_id=warehouse_id, qty=qty, direction=1, unit_cost=cost)]


def _outbound(op: OpContext, mtype: MovementType, product_id: uuid.UUID, warehouse_id: uuid.UUID,
              quantity: Decimal) -> list[StockMovement]:
    product = _lock_product(op.db, product_id)
    if (prev := _existing(op, product_id)) is not None:
        return prev
    _check_stockable(product)
    _check_warehouse(op.db, warehouse_id)
    qty = _qty(quantity)

    balance = _lock_balance(op.db, product.id, warehouse_id)
    available = balance.on_hand - balance.reserved
    if available < qty:
        raise InsufficientStock(f"Stock insuficiente: disponible {available.normalize()}, pedido {qty.normalize()}.")
    balance.on_hand -= qty
    return [_movement(op, mtype=mtype, product=product, warehouse_id=warehouse_id, qty=qty, direction=-1,
                      unit_cost=product.average_cost)]


def receive(op: OpContext, product_id: uuid.UUID, warehouse_id: uuid.UUID, quantity: Decimal,
            unit_cost: Decimal) -> list[StockMovement]:
    return _inbound(op, MovementType.RECEIPT, product_id, warehouse_id, quantity, unit_cost)


def issue(op: OpContext, product_id: uuid.UUID, warehouse_id: uuid.UUID, quantity: Decimal) -> list[StockMovement]:
    return _outbound(op, MovementType.ISSUE, product_id, warehouse_id, quantity)


def adjust(op: OpContext, product_id: uuid.UUID, warehouse_id: uuid.UUID, quantity: Decimal,
           direction: int, unit_cost: Decimal | None = None) -> list[StockMovement]:
    if not op.notes:
        raise InventoryError("Un ajuste de stock requiere un motivo.")
    if direction == 1:
        return _inbound(op, MovementType.ADJUSTMENT_IN, product_id, warehouse_id, quantity, unit_cost)
    if direction == -1:
        return _outbound(op, MovementType.ADJUSTMENT_OUT, product_id, warehouse_id, quantity)
    raise InventoryError("Direccion invalida.")


def transfer(op: OpContext, product_id: uuid.UUID, from_warehouse_id: uuid.UUID, to_warehouse_id: uuid.UUID,
             quantity: Decimal) -> list[StockMovement]:
    if from_warehouse_id == to_warehouse_id:
        raise InventoryError("El deposito de origen y destino no pueden ser el mismo.")
    product = _lock_product(op.db, product_id)
    if (prev := _existing(op, product_id)) is not None:
        return prev
    _check_stockable(product)
    _check_warehouse(op.db, from_warehouse_id)
    _check_warehouse(op.db, to_warehouse_id)
    qty = _qty(quantity)

    balances = {wid: _lock_balance(op.db, product.id, wid) for wid in sorted([from_warehouse_id, to_warehouse_id])}
    source, target = balances[from_warehouse_id], balances[to_warehouse_id]
    available = source.on_hand - source.reserved
    if available < qty:
        raise InsufficientStock(f"Stock insuficiente: disponible {available.normalize()}, pedido {qty.normalize()}.")
    source.on_hand -= qty
    target.on_hand += qty
    group = uuid.uuid4()
    cost = product.average_cost
    return [
        _movement(op, mtype=MovementType.TRANSFER_OUT, product=product, warehouse_id=from_warehouse_id,
                  qty=qty, direction=-1, unit_cost=cost, group_id=group),
        _movement(op, mtype=MovementType.TRANSFER_IN, product=product, warehouse_id=to_warehouse_id,
                  qty=qty, direction=1, unit_cost=cost, group_id=group),
    ]


def reverse(op: OpContext, movement_id: uuid.UUID) -> list[StockMovement]:
    """Revierte un movimiento (o la transferencia completa, si es una de sus
    patas). Nunca edita el original: crea movimientos REVERSAL opuestos."""
    original = op.db.get(StockMovement, movement_id)
    if original is None:
        raise NotFound("Movimiento no encontrado.")
    product = _lock_product(op.db, original.product_id)
    if (prev := _existing(op, product.id)) is not None:
        return prev
    if original.movement_type == MovementType.REVERSAL:
        raise InventoryError("No se puede revertir una reversion.")
    # Movimientos generados por un documento (entrega de venta, recepcion de
    # compra) no se revierten sueltos: quedarian el documento y el stock
    # desalineados. Se corrigen desde el documento (devolucion, etc.).
    from app.tenant_models.purchases import PurchaseOrder
    from app.tenant_models.sales import SalesOrder

    if (original.group_id is not None and op.db.get(SalesOrder, original.group_id) is not None) or (
        original.reference is not None
        and op.db.execute(select(PurchaseOrder.id).where(PurchaseOrder.number == original.reference)).first()
    ):
        raise InventoryError("Este movimiento lo genero un documento: corregirlo desde el pedido u orden de compra.")

    if original.group_id is not None and original.movement_type in (MovementType.TRANSFER_IN, MovementType.TRANSFER_OUT):
        legs = op.db.execute(
            select(StockMovement).where(StockMovement.group_id == original.group_id)
        ).scalars().all()
    else:
        legs = [original]

    already = op.db.execute(
        select(StockMovement.id).where(StockMovement.reverses_movement_id.in_([leg.id for leg in legs]))
    ).first()
    if already is not None:
        raise AlreadyReversed("El movimiento ya fue revertido.")

    total = _total_on_hand(op.db, product.id)
    balances = {wid: _lock_balance(op.db, product.id, wid) for wid in sorted({leg.warehouse_id for leg in legs})}
    group = uuid.uuid4() if len(legs) > 1 else None
    created = []
    for leg in legs:
        balance = balances[leg.warehouse_id]
        if leg.direction == 1:
            # Deshacer una entrada: el stock tiene que seguir ahi.
            if balance.on_hand - balance.reserved < leg.quantity:
                raise InsufficientStock("No se puede revertir: ese stock ya se uso.")
            balance.on_hand -= leg.quantity
            if leg.movement_type != MovementType.TRANSFER_IN:
                remaining = total - leg.quantity
                if remaining > 0:
                    product.average_cost = max(
                        Decimal("0"),
                        ((total * product.average_cost - leg.quantity * leg.unit_cost) / remaining)
                        .quantize(COST_Q, rounding=ROUND_HALF_UP),
                    )
                total = remaining
        else:
            balance.on_hand += leg.quantity
            if leg.movement_type != MovementType.TRANSFER_OUT:
                product.average_cost = _new_average(total, product.average_cost, leg.quantity, leg.unit_cost)
                total += leg.quantity
        created.append(_movement(op, mtype=MovementType.REVERSAL, product=product, warehouse_id=leg.warehouse_id,
                                 qty=leg.quantity, direction=-leg.direction, unit_cost=leg.unit_cost,
                                 group_id=group, reverses=leg.id))
    return created


# --- Reservas (para pedidos de venta) ----------------------------------------
# Una reserva no es un movimiento: no cambia on_hand ni el kardex, solo
# aparta stock (reserved) para que otra venta no lo tome.


def reserve(op: OpContext, product_id: uuid.UUID, warehouse_id: uuid.UUID, quantity: Decimal) -> None:
    product = _lock_product(op.db, product_id)
    _check_stockable(product)
    _check_warehouse(op.db, warehouse_id)
    qty = _qty(quantity)
    balance = _lock_balance(op.db, product.id, warehouse_id)
    available = balance.on_hand - balance.reserved
    if available < qty:
        raise InsufficientStock(
            f"Stock insuficiente para reservar {product.sku}: disponible {available.normalize()}, pedido {qty.normalize()}."
        )
    balance.reserved += qty


def release(op: OpContext, product_id: uuid.UUID, warehouse_id: uuid.UUID, quantity: Decimal) -> None:
    product = _lock_product(op.db, product_id)
    qty = _qty(quantity)
    balance = _lock_balance(op.db, product.id, warehouse_id)
    if balance.reserved < qty:
        raise InventoryError("La reserva a liberar es mayor que lo reservado.")
    balance.reserved -= qty


def issue_reserved(op: OpContext, product_id: uuid.UUID, warehouse_id: uuid.UUID, quantity: Decimal,
                   group_id: uuid.UUID | None = None) -> StockMovement:
    """Entrega stock que ya estaba reservado: baja reserved y on_hand juntos
    y deja el movimiento ISSUE al costo promedio vigente."""
    product = _lock_product(op.db, product_id)
    qty = _qty(quantity)
    balance = _lock_balance(op.db, product.id, warehouse_id)
    if balance.reserved < qty or balance.on_hand < qty:
        raise InventoryError("No hay stock reservado suficiente para entregar.")
    balance.reserved -= qty
    balance.on_hand -= qty
    return _movement(op, mtype=MovementType.ISSUE, product=product, warehouse_id=warehouse_id, qty=qty,
                     direction=-1, unit_cost=product.average_cost, group_id=group_id)
