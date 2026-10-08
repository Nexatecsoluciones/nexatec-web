"""Inventario del ERP: entradas, salidas, ajustes, transferencias,
reversiones, kardex y saldos. Toda la logica (locks, costo promedio,
idempotencia) esta en app/services/inventory.py; aca solo se valida
entrada, se traduce errores a HTTP, se commitea y se audita."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, get_erp_context, require_erp_write
from app.services import inventory
from app.tenant_models.core import Product, Warehouse
from app.tenant_models.inventory import MovementType, StockBalance, StockMovement

router = APIRouter(prefix="/api/erp/{system_access_id}/stock", tags=["erp", "inventory"])

MAX_PAGE = 200

IdempotencyKey = Header(default=None, alias="Idempotency-Key", max_length=100)


class MovementOut(BaseModel):
    id: uuid.UUID
    movement_type: MovementType
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    direction: int
    unit_cost: Decimal
    group_id: uuid.UUID | None
    reference: str | None
    reverses_movement_id: uuid.UUID | None
    notes: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


Qty = Field(gt=0, max_digits=18, decimal_places=4)
Cost = Field(ge=0, max_digits=18, decimal_places=4)


class ReceiptIn(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal = Qty
    unit_cost: Decimal = Cost
    reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class IssueIn(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal = Qty
    reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class AdjustmentIn(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    direction: Literal["IN", "OUT"]
    quantity: Decimal = Qty
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    reason: str = Field(min_length=3, max_length=1000)


class TransferIn(BaseModel):
    product_id: uuid.UUID
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    quantity: Decimal = Qty
    reference: str | None = Field(default=None, max_length=120)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


def _run(ctx: ErpContext, action: str, fn) -> list[StockMovement]:
    try:
        movements = fn()
        ctx.db.commit()
    except inventory.InventoryError as exc:
        ctx.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except IntegrityError:
        # Ultima red: un CHECK de la base (stock negativo) o la unicidad de
        # reverses_movement_id ganaron una carrera que el codigo no vio.
        ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Conflicto de stock, reintentar.")
    log_audit(
        ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action=action,
        resource=f"stock_movement:{movements[0].id}" if movements else None,
        metadata={"movements": [str(m.id) for m in movements]},
    )
    ctx.control_db.commit()
    return movements


def _op(ctx: ErpContext, key: str | None, reference: str | None = None, notes: str | None = None):
    return inventory.OpContext(db=ctx.db, user_id=ctx.user.id, idempotency_key=key, reference=reference, notes=notes)


@router.post("/receipts", response_model=list[MovementOut], status_code=status.HTTP_201_CREATED)
def post_receipt(payload: ReceiptIn, ctx: ErpContext = Depends(require_erp_write), key: str | None = IdempotencyKey):
    op = _op(ctx, key, payload.reference, payload.notes)
    return _run(ctx, "ERP_STOCK_RECEIPT", lambda: inventory.receive(
        op, payload.product_id, payload.warehouse_id, payload.quantity, payload.unit_cost))


@router.post("/issues", response_model=list[MovementOut], status_code=status.HTTP_201_CREATED)
def post_issue(payload: IssueIn, ctx: ErpContext = Depends(require_erp_write), key: str | None = IdempotencyKey):
    op = _op(ctx, key, payload.reference, payload.notes)
    return _run(ctx, "ERP_STOCK_ISSUE", lambda: inventory.issue(
        op, payload.product_id, payload.warehouse_id, payload.quantity))


@router.post("/adjustments", response_model=list[MovementOut], status_code=status.HTTP_201_CREATED)
def post_adjustment(payload: AdjustmentIn, ctx: ErpContext = Depends(require_erp_write), key: str | None = IdempotencyKey):
    op = _op(ctx, key, notes=payload.reason)
    direction = 1 if payload.direction == "IN" else -1
    return _run(ctx, "ERP_STOCK_ADJUSTMENT", lambda: inventory.adjust(
        op, payload.product_id, payload.warehouse_id, payload.quantity, direction, payload.unit_cost))


@router.post("/transfers", response_model=list[MovementOut], status_code=status.HTTP_201_CREATED)
def post_transfer(payload: TransferIn, ctx: ErpContext = Depends(require_erp_write), key: str | None = IdempotencyKey):
    op = _op(ctx, key, payload.reference)
    return _run(ctx, "ERP_STOCK_TRANSFER", lambda: inventory.transfer(
        op, payload.product_id, payload.from_warehouse_id, payload.to_warehouse_id, payload.quantity))


@router.post("/movements/{movement_id}/reverse", response_model=list[MovementOut], status_code=status.HTTP_201_CREATED)
def post_reverse(movement_id: uuid.UUID, payload: ReverseIn, ctx: ErpContext = Depends(require_erp_write),
                 key: str | None = IdempotencyKey):
    op = _op(ctx, key, notes=payload.reason)
    return _run(ctx, "ERP_STOCK_REVERSAL", lambda: inventory.reverse(op, movement_id))


class MovementPage(BaseModel):
    total: int
    items: list[MovementOut]


@router.get("/movements", response_model=MovementPage)
def list_movements(
    ctx: ErpContext = Depends(get_erp_context),
    product_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    """Kardex: mas reciente primero, siempre paginado."""
    stmt = select(StockMovement)
    if product_id:
        stmt = stmt.where(StockMovement.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(StockMovement.warehouse_id == warehouse_id)
    if date_from:
        stmt = stmt.where(StockMovement.created_at >= date_from)
    if date_to:
        stmt = stmt.where(StockMovement.created_at < date_to)
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = ctx.db.execute(
        stmt.order_by(StockMovement.created_at.desc(), StockMovement.id).limit(limit).offset(offset)
    ).scalars().all()
    return MovementPage(total=total, items=items)


class BalanceOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    average_cost: Decimal
    stock_value: Decimal


class BalancePage(BaseModel):
    total: int
    items: list[BalanceOut]


@router.get("/balances", response_model=BalancePage)
def list_balances(
    ctx: ErpContext = Depends(get_erp_context),
    product_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    only_positive: bool = False,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(StockBalance, Product.sku, Product.name, Product.average_cost, Warehouse.code)
        .join(Product, Product.id == StockBalance.product_id)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
    )
    if product_id:
        stmt = stmt.where(StockBalance.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
    if only_positive:
        stmt = stmt.where(StockBalance.on_hand > 0)
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = ctx.db.execute(stmt.order_by(Product.name, Warehouse.code).limit(limit).offset(offset)).all()
    return BalancePage(total=total, items=[
        BalanceOut(
            product_id=b.product_id, sku=sku, product_name=name, warehouse_id=b.warehouse_id,
            warehouse_code=wcode, on_hand=b.on_hand, reserved=b.reserved, available=b.on_hand - b.reserved,
            average_cost=avg, stock_value=(b.on_hand * avg).quantize(Decimal("0.01")),
        )
        for b, sku, name, avg, wcode in rows
    ])
