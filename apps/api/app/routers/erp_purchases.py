"""Compras y cuentas por pagar. La logica esta en app/services/purchases.py."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, get_erp_context, require_erp_write
from app.services import inventory, purchases
from app.services.receivables import AllocationInput, local_today
from app.services.sales import SalesError
from app.tenant_models.purchases import PurchaseOrder, PurchaseOrderStatus, SupplierInvoice, SupplierPayment
from app.tenant_models.receivables import InvoiceStatus, PaymentMethod, ReceiptStatus

router = APIRouter(prefix="/api/erp/{system_access_id}", tags=["erp", "purchases"])

MAX_PAGE = 200
Money0 = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)


class POLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    description: str | None = Field(default=None, max_length=200)


class POIn(BaseModel):
    supplier_id: uuid.UUID
    warehouse_id: uuid.UUID
    expected_date: date | None = None
    lines: list[POLineIn] = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class POLinesIn(BaseModel):
    lines: list[POLineIn] = Field(min_length=1, max_length=200)


class ReceiveLineIn(BaseModel):
    line_no: int = Field(ge=1)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


class ReceiveIn(BaseModel):
    lines: list[ReceiveLineIn] = Field(min_length=1, max_length=200)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class POLineOut(BaseModel):
    line_no: int
    product_id: uuid.UUID
    description: str
    quantity: Decimal
    quantity_received: Decimal
    unit_price: Decimal
    tax_code: str
    tax_rate: Decimal
    line_net: Decimal
    line_tax: Decimal
    line_total: Decimal
    model_config = ConfigDict(from_attributes=True)


class POOut(BaseModel):
    id: uuid.UUID
    number: str
    supplier_id: uuid.UUID
    warehouse_id: uuid.UUID
    status: PurchaseOrderStatus
    currency: str
    expected_date: date | None
    subtotal_net: Decimal
    tax_total: Decimal
    total: Decimal
    notes: str | None
    close_reason: str | None
    created_at: datetime
    lines: list[POLineOut]
    model_config = ConfigDict(from_attributes=True)


class POPage(BaseModel):
    total: int
    items: list[POOut]


class SupplierInvoiceIn(BaseModel):
    supplier_id: uuid.UUID
    supplier_invoice_number: str = Field(min_length=1, max_length=30, pattern=r"^[0-9A-Za-z\-/]+$")
    supplier_timbrado: str | None = Field(default=None, max_length=20, pattern=r"^[0-9]+$")
    issue_date: date
    due_date: date | None = None
    purchase_order_id: uuid.UUID | None = None
    taxable_10: Decimal = Money0
    vat_10: Decimal = Money0
    taxable_5: Decimal = Money0
    vat_5: Decimal = Money0
    exempt: Decimal = Money0


class SupplierInvoiceOut(BaseModel):
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_invoice_number: str
    supplier_timbrado: str | None
    purchase_order_id: uuid.UUID | None
    status: InvoiceStatus
    issue_date: date
    due_date: date
    currency: str
    taxable_10: Decimal
    vat_10: Decimal
    taxable_5: Decimal
    vat_5: Decimal
    exempt: Decimal
    total: Decimal
    balance_due: Decimal
    void_reason: str | None
    model_config = ConfigDict(from_attributes=True)


class SupplierInvoicePage(BaseModel):
    total: int
    items: list[SupplierInvoiceOut]


class AllocationIn(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


class AllocationOut(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal
    model_config = ConfigDict(from_attributes=True)


class PaymentIn(BaseModel):
    supplier_id: uuid.UUID
    method: PaymentMethod
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    allocations: list[AllocationIn] = Field(default_factory=list, max_length=200)
    reference: str | None = Field(default=None, max_length=120)


class ApplyIn(BaseModel):
    allocations: list[AllocationIn] = Field(min_length=1, max_length=200)


class PaymentOut(BaseModel):
    id: uuid.UUID
    number: str
    supplier_id: uuid.UUID
    payment_date: date
    method: PaymentMethod
    currency: str
    amount: Decimal
    unapplied_amount: Decimal
    status: ReceiptStatus
    reference: str | None
    void_reason: str | None
    allocations: list[AllocationOut]
    model_config = ConfigDict(from_attributes=True)


class PaymentPage(BaseModel):
    total: int
    items: list[PaymentOut]


class APAgingRow(BaseModel):
    supplier_id: uuid.UUID
    supplier_name: str
    current: Decimal
    d1_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    d90_plus: Decimal
    total_due: Decimal
    unapplied_advances: Decimal
    net_balance: Decimal


def _run(ctx: ErpContext, action: str, fn, resource: str, label=lambda o: o.number):
    try:
        obj = fn()
        ctx.db.commit()
    except (SalesError, inventory.InventoryError) as exc:
        ctx.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Conflicto o documento duplicado.")
    ctx.db.refresh(obj)
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action=action,
              resource=f"{resource}:{obj.id}", metadata={"number": label(obj), "status": obj.status.value})
    ctx.control_db.commit()
    return obj


def _alloc(items: list[AllocationIn]) -> list[AllocationInput]:
    return [AllocationInput(invoice_id=a.invoice_id, amount=a.amount) for a in items]


def _po_lines(items: list[POLineIn]) -> list[purchases.POLineInput]:
    return [purchases.POLineInput(**li.model_dump()) for li in items]


def _page(ctx: ErpContext, stmt, order_by, limit: int, offset: int):
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    return total, ctx.db.execute(stmt.order_by(*order_by).limit(limit).offset(offset)).scalars().all()


# --- Ordenes de compra -------------------------------------------------------------


@router.post("/purchases/orders", response_model=POOut, status_code=status.HTTP_201_CREATED)
def create_po(payload: POIn, ctx: ErpContext = Depends(require_erp_write)):
    return _run(ctx, "ERP_PURCHASE_ORDER_CREATED", lambda: purchases.create_po(
        ctx.db, user_id=ctx.user.id, supplier_id=payload.supplier_id, warehouse_id=payload.warehouse_id,
        lines=_po_lines(payload.lines), expected_date=payload.expected_date, notes=payload.notes), "purchase_order")


@router.put("/purchases/orders/{po_id}/lines", response_model=POOut)
def replace_po_lines(po_id: uuid.UUID, payload: POLinesIn, ctx: ErpContext = Depends(require_erp_write)):
    return _run(ctx, "ERP_PURCHASE_ORDER_UPDATED",
                lambda: purchases.replace_po_lines(ctx.db, po_id, _po_lines(payload.lines)), "purchase_order")


@router.post("/purchases/orders/{po_id}/confirm", response_model=POOut)
def confirm_po(po_id: uuid.UUID, ctx: ErpContext = Depends(require_erp_write)):
    return _run(ctx, "ERP_PURCHASE_ORDER_CONFIRMED", lambda: purchases.confirm_po(ctx.db, po_id), "purchase_order")


@router.post("/purchases/orders/{po_id}/receive", response_model=POOut)
def receive_po(po_id: uuid.UUID, payload: ReceiveIn, ctx: ErpContext = Depends(require_erp_write),
               key: str | None = Header(default=None, alias="Idempotency-Key", max_length=90)):
    items = [purchases.ReceiveItem(line_no=li.line_no, quantity=li.quantity) for li in payload.lines]
    return _run(ctx, "ERP_PURCHASE_ORDER_RECEIVED",
                lambda: purchases.receive(ctx.db, po_id, ctx.user.id, items, key), "purchase_order")


@router.post("/purchases/orders/{po_id}/close", response_model=POOut)
def close_po(po_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require_erp_write)):
    return _run(ctx, "ERP_PURCHASE_ORDER_CLOSED",
                lambda: purchases.cancel_or_close_po(ctx.db, po_id, payload.reason), "purchase_order")


@router.get("/purchases/orders/{po_id}", response_model=POOut)
def get_po(po_id: uuid.UUID, ctx: ErpContext = Depends(get_erp_context)):
    po = ctx.db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Orden de compra no encontrada.")
    return po


@router.get("/purchases/orders", response_model=POPage)
def list_pos(ctx: ErpContext = Depends(get_erp_context), supplier_id: uuid.UUID | None = None,
             status_filter: PurchaseOrderStatus | None = Query(default=None, alias="status"),
             limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0)):
    stmt = select(PurchaseOrder)
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    if status_filter:
        stmt = stmt.where(PurchaseOrder.status == status_filter)
    total, items = _page(ctx, stmt, [PurchaseOrder.created_at.desc()], limit, offset)
    return POPage(total=total, items=items)


# --- Facturas de proveedor -----------------------------------------------------------


@router.post("/supplier-invoices", response_model=SupplierInvoiceOut, status_code=status.HTTP_201_CREATED)
def register_supplier_invoice(payload: SupplierInvoiceIn, ctx: ErpContext = Depends(require_erp_write)):
    b = purchases.Breakdown(taxable_10=payload.taxable_10, vat_10=payload.vat_10, taxable_5=payload.taxable_5,
                            vat_5=payload.vat_5, exempt=payload.exempt)
    return _run(ctx, "ERP_SUPPLIER_INVOICE_REGISTERED", lambda: purchases.register_supplier_invoice(
        ctx.db, user_id=ctx.user.id, supplier_id=payload.supplier_id,
        supplier_invoice_number=payload.supplier_invoice_number, supplier_timbrado=payload.supplier_timbrado,
        issue_date=payload.issue_date, due_date=payload.due_date, breakdown=b,
        purchase_order_id=payload.purchase_order_id), "supplier_invoice", label=lambda o: o.supplier_invoice_number)


@router.post("/supplier-invoices/{invoice_id}/void", response_model=SupplierInvoiceOut)
def void_supplier_invoice(invoice_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require_erp_write)):
    return _run(ctx, "ERP_SUPPLIER_INVOICE_VOIDED",
                lambda: purchases.void_supplier_invoice(ctx.db, invoice_id, payload.reason), "supplier_invoice",
                label=lambda o: o.supplier_invoice_number)


@router.get("/supplier-invoices/{invoice_id}", response_model=SupplierInvoiceOut)
def get_supplier_invoice(invoice_id: uuid.UUID, ctx: ErpContext = Depends(get_erp_context)):
    inv = ctx.db.get(SupplierInvoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Factura de proveedor no encontrada.")
    return inv


@router.get("/supplier-invoices", response_model=SupplierInvoicePage)
def list_supplier_invoices(ctx: ErpContext = Depends(get_erp_context), supplier_id: uuid.UUID | None = None,
                           open_only: bool = False, limit: int = Query(default=50, ge=1, le=MAX_PAGE),
                           offset: int = Query(default=0, ge=0)):
    stmt = select(SupplierInvoice)
    if supplier_id:
        stmt = stmt.where(SupplierInvoice.supplier_id == supplier_id)
    if open_only:
        stmt = stmt.where(SupplierInvoice.status == InvoiceStatus.ISSUED, SupplierInvoice.balance_due > 0)
    total, items = _page(ctx, stmt, [SupplierInvoice.due_date, SupplierInvoice.created_at], limit, offset)
    return SupplierInvoicePage(total=total, items=items)


# --- Pagos -------------------------------------------------------------------------------


@router.post("/supplier-payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def post_payment(payload: PaymentIn, ctx: ErpContext = Depends(require_erp_write),
                 key: str | None = Header(default=None, alias="Idempotency-Key", max_length=100)):
    def fn():
        return purchases.post_payment(ctx.db, user_id=ctx.user.id, supplier_id=payload.supplier_id,
                                      method=payload.method, amount=payload.amount,
                                      allocations=_alloc(payload.allocations), reference=payload.reference,
                                      idempotency_key=key)
    try:
        return _run(ctx, "ERP_SUPPLIER_PAYMENT_POSTED", fn, "supplier_payment")
    except HTTPException as exc:
        if key and exc.status_code == status.HTTP_409_CONFLICT:
            prev = ctx.db.execute(select(SupplierPayment).where(SupplierPayment.idempotency_key == key)).scalar_one_or_none()
            if prev is not None:
                return prev
        raise


@router.post("/supplier-payments/{payment_id}/apply", response_model=PaymentOut)
def apply_payment(payment_id: uuid.UUID, payload: ApplyIn, ctx: ErpContext = Depends(require_erp_write)):
    return _run(ctx, "ERP_SUPPLIER_PAYMENT_APPLIED",
                lambda: purchases.apply_payment(ctx.db, payment_id, _alloc(payload.allocations)), "supplier_payment")


@router.post("/supplier-payments/{payment_id}/void", response_model=PaymentOut)
def void_payment(payment_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require_erp_write)):
    return _run(ctx, "ERP_SUPPLIER_PAYMENT_VOIDED",
                lambda: purchases.void_payment(ctx.db, payment_id, payload.reason), "supplier_payment")


@router.get("/supplier-payments", response_model=PaymentPage)
def list_payments(ctx: ErpContext = Depends(get_erp_context), supplier_id: uuid.UUID | None = None,
                  limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0)):
    stmt = select(SupplierPayment)
    if supplier_id:
        stmt = stmt.where(SupplierPayment.supplier_id == supplier_id)
    total, items = _page(ctx, stmt, [SupplierPayment.created_at.desc()], limit, offset)
    return PaymentPage(total=total, items=items)


@router.get("/payables/aging", response_model=list[APAgingRow])
def get_ap_aging(ctx: ErpContext = Depends(get_erp_context), as_of: date | None = None):
    return purchases.ap_aging(ctx.db, as_of or local_today(ctx.db))
