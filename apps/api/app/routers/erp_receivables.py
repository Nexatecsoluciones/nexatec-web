"""Facturas internas (simulacion), cobros y cuentas por cobrar. La logica
esta en app/services/receivables.py."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, computed_field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, get_erp_context, require
from app.services import receivables
from app.services.sales import SalesError
from app.tenant_models.receivables import (
    CreditNoteKind,
    CustomerReceipt,
    SalesCreditNote,
    FiscalStatus,
    InvoiceStatus,
    PaymentMethod,
    ReceiptStatus,
    SalesInvoice,
)
from app.tenant_models.sales import PaymentCondition

router = APIRouter(prefix="/api/erp/{system_access_id}", tags=["erp", "receivables"])

MAX_PAGE = 200
Money = Field(gt=0, max_digits=18, decimal_places=2)


class InvoiceOut(BaseModel):
    id: uuid.UUID
    number: str
    order_id: uuid.UUID
    customer_id: uuid.UUID
    status: InvoiceStatus
    fiscal_status: FiscalStatus
    payment_condition: PaymentCondition
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

    @computed_field
    @property
    def legal_notice(self) -> str | None:
        # Cualquier cliente (pantalla, PDF, export) recibe el aviso junto al dato.
        if self.fiscal_status == FiscalStatus.INTERNAL_SIMULATION:
            return receivables.LEGAL_NOTICE_SIMULATION
        return None


class InvoicePage(BaseModel):
    total: int
    items: list[InvoiceOut]


class AllocationIn(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal = Money


class AllocationOut(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal
    model_config = ConfigDict(from_attributes=True)


class ReceiptIn(BaseModel):
    customer_id: uuid.UUID
    method: PaymentMethod
    amount: Decimal = Money
    allocations: list[AllocationIn] = Field(default_factory=list, max_length=200)
    reference: str | None = Field(default=None, max_length=120)


class ApplyIn(BaseModel):
    allocations: list[AllocationIn] = Field(min_length=1, max_length=200)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class ReceiptOut(BaseModel):
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    receipt_date: date
    method: PaymentMethod
    currency: str
    amount: Decimal
    unapplied_amount: Decimal
    status: ReceiptStatus
    reference: str | None
    void_reason: str | None
    allocations: list[AllocationOut]
    model_config = ConfigDict(from_attributes=True)


class ReceiptPage(BaseModel):
    total: int
    items: list[ReceiptOut]


class AgingRow(BaseModel):
    customer_id: uuid.UUID
    customer_name: str
    current: Decimal
    d1_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    d90_plus: Decimal
    total_due: Decimal
    unapplied_advances: Decimal
    net_balance: Decimal


class StatementRow(BaseModel):
    date: date
    kind: str
    number: str
    debit: Decimal
    credit: Decimal
    voided: bool
    balance: Decimal


def _run(ctx: ErpContext, action: str, fn, resource_prefix: str):
    try:
        obj = fn()
        ctx.db.commit()
    except SalesError as exc:
        ctx.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Conflicto al guardar, reintentar.")
    ctx.db.refresh(obj)
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action=action,
              resource=f"{resource_prefix}:{obj.id}", metadata={"number": obj.number, "status": getattr(getattr(obj, "status", None), "value", None)})
    ctx.control_db.commit()
    return obj


def _alloc(items: list[AllocationIn]) -> list[receivables.AllocationInput]:
    return [receivables.AllocationInput(invoice_id=a.invoice_id, amount=a.amount) for a in items]


# --- Facturas -------------------------------------------------------------------


@router.post("/sales/orders/{order_id}/invoice", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def invoice_order(order_id: uuid.UUID, ctx: ErpContext = Depends(require("receivables:write"))):
    return _run(ctx, "ERP_INVOICE_ISSUED", lambda: receivables.invoice_order(ctx.db, order_id, ctx.user.id), "sales_invoice")


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceOut)
def void_invoice(invoice_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require("receivables:write"))):
    return _run(ctx, "ERP_INVOICE_VOIDED", lambda: receivables.void_invoice(ctx.db, invoice_id, payload.reason), "sales_invoice")


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: uuid.UUID, ctx: ErpContext = Depends(require("receivables:read"))):
    inv = ctx.db.get(SalesInvoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada.")
    return inv


@router.get("/invoices", response_model=InvoicePage)
def list_invoices(
    ctx: ErpContext = Depends(require("receivables:read")),
    customer_id: uuid.UUID | None = None,
    status_filter: InvoiceStatus | None = Query(default=None, alias="status"),
    open_only: bool = False,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(SalesInvoice)
    if customer_id:
        stmt = stmt.where(SalesInvoice.customer_id == customer_id)
    if status_filter:
        stmt = stmt.where(SalesInvoice.status == status_filter)
    if open_only:
        stmt = stmt.where(SalesInvoice.status == InvoiceStatus.ISSUED, SalesInvoice.balance_due > 0)
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = ctx.db.execute(stmt.order_by(SalesInvoice.issue_date.desc(), SalesInvoice.number.desc())
                           .limit(limit).offset(offset)).scalars().all()
    return InvoicePage(total=total, items=items)


# --- Cobros ---------------------------------------------------------------------


@router.post("/receipts", response_model=ReceiptOut, status_code=status.HTTP_201_CREATED)
def post_receipt(payload: ReceiptIn, ctx: ErpContext = Depends(require("receivables:write")),
                 key: str | None = Header(default=None, alias="Idempotency-Key", max_length=100)):
    def fn():
        return receivables.post_receipt(
            ctx.db, user_id=ctx.user.id, customer_id=payload.customer_id, method=payload.method,
            amount=payload.amount, allocations=_alloc(payload.allocations), reference=payload.reference,
            idempotency_key=key,
        )

    try:
        return _run(ctx, "ERP_RECEIPT_POSTED", fn, "customer_receipt")
    except HTTPException as exc:
        # Dos reintentos simultaneos con la misma clave: el segundo choca con
        # el indice unico; se devuelve el cobro que gano, no un error.
        if key and exc.status_code == status.HTTP_409_CONFLICT:
            prev = ctx.db.execute(select(CustomerReceipt).where(CustomerReceipt.idempotency_key == key)).scalar_one_or_none()
            if prev is not None:
                return prev
        raise


@router.post("/receipts/{receipt_id}/apply", response_model=ReceiptOut)
def apply_receipt(receipt_id: uuid.UUID, payload: ApplyIn, ctx: ErpContext = Depends(require("receivables:write"))):
    return _run(ctx, "ERP_RECEIPT_APPLIED",
                lambda: receivables.apply_receipt(ctx.db, receipt_id, _alloc(payload.allocations)), "customer_receipt")


@router.post("/receipts/{receipt_id}/void", response_model=ReceiptOut)
def void_receipt(receipt_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require("receivables:write"))):
    return _run(ctx, "ERP_RECEIPT_VOIDED", lambda: receivables.void_receipt(ctx.db, receipt_id, payload.reason),
                "customer_receipt")


@router.get("/receipts/{receipt_id}", response_model=ReceiptOut)
def get_receipt(receipt_id: uuid.UUID, ctx: ErpContext = Depends(require("receivables:read"))):
    rc = ctx.db.get(CustomerReceipt, receipt_id)
    if rc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cobro no encontrado.")
    return rc


@router.get("/receipts", response_model=ReceiptPage)
def list_receipts(
    ctx: ErpContext = Depends(require("receivables:read")),
    customer_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(CustomerReceipt)
    if customer_id:
        stmt = stmt.where(CustomerReceipt.customer_id == customer_id)
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = ctx.db.execute(stmt.order_by(CustomerReceipt.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return ReceiptPage(total=total, items=items)


# --- Cuentas por cobrar -----------------------------------------------------------


@router.get("/receivables/aging", response_model=list[AgingRow])
def get_aging(ctx: ErpContext = Depends(require("receivables:read")), as_of: date | None = None):
    return receivables.aging(ctx.db, as_of or receivables.local_today(ctx.db))


@router.get("/receivables/customers/{customer_id}/statement", response_model=list[StatementRow])
def get_statement(customer_id: uuid.UUID, ctx: ErpContext = Depends(require("receivables:read"))):
    return receivables.statement(ctx.db, customer_id)



# --- Notas de credito ----------------------------------------------------------------


class CreditLineIn(BaseModel):
    line_no: int = Field(ge=1)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=4)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)


class CreditNoteIn(BaseModel):
    kind: CreditNoteKind
    reason: str = Field(min_length=3, max_length=1000)
    restock: bool = True
    lines: list[CreditLineIn] = Field(min_length=1, max_length=200)


class CreditNoteLineOut(BaseModel):
    order_line_no: int
    description: str
    quantity: Decimal
    tax_rate: Decimal
    line_net: Decimal
    line_tax: Decimal
    line_total: Decimal
    model_config = ConfigDict(from_attributes=True)


class CreditNoteOut(BaseModel):
    id: uuid.UUID
    number: str
    invoice_id: uuid.UUID
    customer_id: uuid.UUID
    kind: CreditNoteKind
    fiscal_status: FiscalStatus
    issue_date: date
    reason: str
    restocked: bool
    taxable_10: Decimal
    vat_10: Decimal
    taxable_5: Decimal
    vat_5: Decimal
    exempt: Decimal
    total: Decimal
    applied_amount: Decimal
    unapplied_amount: Decimal
    lines: list[CreditNoteLineOut]
    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def legal_notice(self) -> str | None:
        if self.fiscal_status == FiscalStatus.INTERNAL_SIMULATION:
            return receivables.LEGAL_NOTICE_SIMULATION
        return None


class CreditNotePage(BaseModel):
    total: int
    items: list[CreditNoteOut]


@router.post("/invoices/{invoice_id}/credit-notes", response_model=CreditNoteOut, status_code=status.HTTP_201_CREATED)
def issue_credit_note(invoice_id: uuid.UUID, payload: CreditNoteIn, ctx: ErpContext = Depends(require("receivables:write"))):
    for ln in payload.lines:
        needed = ln.quantity if payload.kind == CreditNoteKind.RETURN else ln.amount
        if needed is None:
            raise HTTPException(status_code=422, detail="Devolucion: indicar cantidad. Bonificacion: indicar monto.")
    items = [receivables.CreditLineInput(line_no=ln.line_no, quantity=ln.quantity, amount=ln.amount) for ln in payload.lines]
    return _run(ctx, "ERP_CREDIT_NOTE_ISSUED", lambda: receivables.issue_credit_note(
        ctx.db, invoice_id=invoice_id, kind=payload.kind, reason=payload.reason, restock=payload.restock,
        lines=items, user_id=ctx.user.id), "sales_credit_note")


@router.get("/credit-notes/{credit_note_id}", response_model=CreditNoteOut)
def get_credit_note(credit_note_id: uuid.UUID, ctx: ErpContext = Depends(require("receivables:read"))):
    note = ctx.db.get(SalesCreditNote, credit_note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nota de credito no encontrada.")
    return note


@router.get("/credit-notes", response_model=CreditNotePage)
def list_credit_notes(ctx: ErpContext = Depends(require("receivables:read")), invoice_id: uuid.UUID | None = None,
                      customer_id: uuid.UUID | None = None, limit: int = Query(default=50, ge=1, le=MAX_PAGE),
                      offset: int = Query(default=0, ge=0)):
    stmt = select(SalesCreditNote)
    if invoice_id:
        stmt = stmt.where(SalesCreditNote.invoice_id == invoice_id)
    if customer_id:
        stmt = stmt.where(SalesCreditNote.customer_id == customer_id)
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = ctx.db.execute(stmt.order_by(SalesCreditNote.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return CreditNotePage(total=total, items=items)
