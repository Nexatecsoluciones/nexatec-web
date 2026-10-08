"""Pedidos de venta. La logica esta en app/services/sales.py; aca se valida
entrada, se traduce errores a HTTP, se commitea (una request = una
transaccion) y se audita."""

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, get_erp_context, require
from app.services import inventory, sales
from app.tenant_models.sales import PaymentCondition, SalesOrder, SalesOrderStatus

router = APIRouter(prefix="/api/erp/{system_access_id}/sales", tags=["erp", "sales"])

MAX_PAGE = 200


class LineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    description: str | None = Field(default=None, max_length=200)


class OrderIn(BaseModel):
    customer_id: uuid.UUID
    warehouse_id: uuid.UUID
    payment_condition: PaymentCondition
    lines: list[LineIn] = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class LinesIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1, max_length=200)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class LineOut(BaseModel):
    line_no: int
    product_id: uuid.UUID
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount_pct: Decimal
    tax_code: str
    tax_rate: Decimal
    line_net: Decimal
    line_tax: Decimal
    line_total: Decimal
    unit_cost: Decimal | None
    quantity_returned: Decimal
    amount_credited: Decimal
    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    warehouse_id: uuid.UUID
    status: SalesOrderStatus
    payment_condition: PaymentCondition
    currency: str
    subtotal_net: Decimal
    tax_total: Decimal
    total: Decimal
    notes: str | None
    cancel_reason: str | None
    created_at: datetime
    confirmed_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    lines: list[LineOut]
    model_config = ConfigDict(from_attributes=True)


class OrderSummary(BaseModel):
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    status: SalesOrderStatus
    payment_condition: PaymentCondition
    total: Decimal
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OrderPage(BaseModel):
    total: int
    items: list[OrderSummary]


def _lines(items: list[LineIn]) -> list[sales.LineInput]:
    return [sales.LineInput(**li.model_dump()) for li in items]


def _run(ctx: ErpContext, action: str, fn) -> SalesOrder:
    try:
        order = fn()
        ctx.db.commit()
    except (sales.SalesError, inventory.InventoryError) as exc:
        ctx.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Conflicto al guardar el pedido, reintentar.")
    ctx.db.refresh(order)
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action=action,
              resource=f"sales_order:{order.id}", metadata={"number": order.number, "status": order.status.value})
    ctx.control_db.commit()
    return order


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderIn, ctx: ErpContext = Depends(require("sales:write"))):
    return _run(ctx, "ERP_SALES_ORDER_CREATED", lambda: sales.create_order(
        ctx.db, user_id=ctx.user.id, customer_id=payload.customer_id, warehouse_id=payload.warehouse_id,
        payment_condition=payload.payment_condition, lines=_lines(payload.lines), notes=payload.notes))


@router.put("/orders/{order_id}/lines", response_model=OrderOut)
def replace_lines(order_id: uuid.UUID, payload: LinesIn, ctx: ErpContext = Depends(require("sales:write"))):
    return _run(ctx, "ERP_SALES_ORDER_UPDATED", lambda: sales.replace_lines(ctx.db, order_id, _lines(payload.lines)))


@router.post("/orders/{order_id}/confirm", response_model=OrderOut)
def confirm(order_id: uuid.UUID, ctx: ErpContext = Depends(require("sales:write"))):
    return _run(ctx, "ERP_SALES_ORDER_CONFIRMED", lambda: sales.confirm(ctx.db, order_id, ctx.user.id))


@router.post("/orders/{order_id}/deliver", response_model=OrderOut)
def deliver(order_id: uuid.UUID, ctx: ErpContext = Depends(require("sales:deliver"))):
    return _run(ctx, "ERP_SALES_ORDER_DELIVERED", lambda: sales.deliver(ctx.db, order_id, ctx.user.id))


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel(order_id: uuid.UUID, payload: CancelIn, ctx: ErpContext = Depends(require("sales:write"))):
    return _run(ctx, "ERP_SALES_ORDER_CANCELLED", lambda: sales.cancel(ctx.db, order_id, ctx.user.id, payload.reason))


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: uuid.UUID, ctx: ErpContext = Depends(require("sales:read"))):
    order = ctx.db.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado.")
    return order


@router.get("/orders", response_model=OrderPage)
def list_orders(
    ctx: ErpContext = Depends(require("sales:read")),
    status_filter: SalesOrderStatus | None = Query(default=None, alias="status"),
    customer_id: uuid.UUID | None = None,
    number: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(SalesOrder)
    if status_filter:
        stmt = stmt.where(SalesOrder.status == status_filter)
    if customer_id:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    if number:
        stmt = stmt.where(SalesOrder.number.ilike(f"%{number}%"))
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = ctx.db.execute(stmt.order_by(SalesOrder.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return OrderPage(total=total, items=items)
