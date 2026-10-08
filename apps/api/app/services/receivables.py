"""Facturacion interna, cobros y cuentas por cobrar.

Locks: facturas siempre en orden de id (FOR UPDATE) antes de tocar saldos;
recibos antes que sus facturas. Asi dos cobros concurrentes sobre la misma
factura se serializan y la base (CHECK balance_due >= 0) es la ultima red
contra la sobreaplicacion.

Nada hace commit: una request = una transaccion."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.sales import NotFound, SalesError, next_number
from app.tenant_models.core import Company, Party
from app.tenant_models.receivables import (
    CustomerReceipt,
    FiscalStatus,
    InvoiceStatus,
    PaymentMethod,
    ReceiptAllocation,
    ReceiptStatus,
    SalesInvoice,
)
from app.tenant_models.sales import PaymentCondition, SalesOrder, SalesOrderStatus

LEGAL_NOTICE_SIMULATION = "DOCUMENTO DE SIMULACIÓN — SIN VALIDEZ TRIBUTARIA"


class ReceivablesError(SalesError):
    pass


class Conflict(ReceivablesError):
    status_code = 409


@dataclass
class AllocationInput:
    invoice_id: uuid.UUID
    amount: Decimal


def local_today(db: Session) -> date:
    company = db.get(Company, 1)
    tz = ZoneInfo(company.timezone if company else "America/Asuncion")
    return datetime.now(timezone.utc).astimezone(tz).date()


def _lock_invoices(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, SalesInvoice]:
    rows = db.execute(
        select(SalesInvoice).where(SalesInvoice.id.in_(ids)).order_by(SalesInvoice.id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalars().all()
    return {inv.id: inv for inv in rows}


def invoice_order(db: Session, order_id: uuid.UUID, user_id: uuid.UUID | None) -> SalesInvoice:
    order = db.execute(
        select(SalesOrder).where(SalesOrder.id == order_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if order is None:
        raise NotFound("Pedido no encontrado.")
    if order.status != SalesOrderStatus.DELIVERED:
        raise Conflict("Solo se factura un pedido entregado.")
    existing = db.execute(
        select(SalesInvoice).where(SalesInvoice.order_id == order.id, SalesInvoice.status == InvoiceStatus.ISSUED)
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"El pedido ya tiene la factura {existing.number}.")

    zero = Decimal("0")
    b = {"taxable_10": zero, "vat_10": zero, "taxable_5": zero, "vat_5": zero, "exempt": zero}
    for line in order.lines:
        if line.tax_rate == Decimal("10"):
            b["taxable_10"] += line.line_net
            b["vat_10"] += line.line_tax
        elif line.tax_rate == Decimal("5"):
            b["taxable_5"] += line.line_net
            b["vat_5"] += line.line_tax
        elif line.tax_rate == 0:
            b["exempt"] += line.line_total
        else:
            raise ReceivablesError(f"Tasa de IVA no soportada en factura: {line.tax_rate}.")
    if sum(b.values()) != order.total:
        raise ReceivablesError("El desglose de IVA no cuadra con el total del pedido.")

    customer = db.get(Party, order.customer_id)
    issue = local_today(db)
    terms = customer.payment_terms_days if order.payment_condition == PaymentCondition.CREDIT else 0
    invoice = SalesInvoice(
        id=uuid.uuid4(), number=next_number(db, "INVOICE_INTERNAL"), order_id=order.id,
        customer_id=order.customer_id, status=InvoiceStatus.ISSUED, fiscal_status=FiscalStatus.INTERNAL_SIMULATION,
        payment_condition=order.payment_condition, issue_date=issue, due_date=issue + timedelta(days=terms),
        currency=order.currency, total=order.total, balance_due=order.total, created_by_user_id=user_id, **b,
    )
    db.add(invoice)
    return invoice


def void_invoice(db: Session, invoice_id: uuid.UUID, reason: str) -> SalesInvoice:
    invoices = _lock_invoices(db, [invoice_id])
    invoice = invoices.get(invoice_id)
    if invoice is None:
        raise NotFound("Factura no encontrada.")
    if invoice.status == InvoiceStatus.VOIDED:
        return invoice
    if invoice.balance_due != invoice.total:
        raise Conflict("La factura tiene cobros aplicados: anular primero esos cobros.")
    invoice.status = InvoiceStatus.VOIDED
    invoice.balance_due = Decimal("0")
    invoice.void_reason = reason
    invoice.voided_at = datetime.now(timezone.utc)
    return invoice


def _apply(db: Session, receipt: CustomerReceipt, allocations: list[AllocationInput]) -> None:
    if not allocations:
        return
    ids = [a.invoice_id for a in allocations]
    if len(set(ids)) != len(ids):
        raise ReceivablesError("Una factura aparece dos veces en la aplicacion.")
    invoices = _lock_invoices(db, ids)
    total = Decimal("0")
    for a in allocations:
        if a.amount <= 0:
            raise ReceivablesError("Cada aplicacion tiene que ser mayor a cero.")
        inv = invoices.get(a.invoice_id)
        if inv is None or inv.customer_id != receipt.customer_id:
            raise NotFound("Factura no encontrada para este cliente.")
        if inv.status != InvoiceStatus.ISSUED:
            raise Conflict(f"La factura {inv.number} no esta vigente.")
        if a.amount > inv.balance_due:
            raise Conflict(f"La aplicacion supera el saldo de la factura {inv.number} ({inv.balance_due}).")
        total += a.amount
    if total > receipt.unapplied_amount:
        raise Conflict("Lo aplicado supera el monto disponible del cobro.")
    for a in allocations:
        invoices[a.invoice_id].balance_due -= a.amount
        receipt.allocations.append(ReceiptAllocation(invoice_id=a.invoice_id, amount=a.amount))
    receipt.unapplied_amount -= total


def post_receipt(db: Session, *, user_id: uuid.UUID | None, customer_id: uuid.UUID, method: PaymentMethod,
                 amount: Decimal, allocations: list[AllocationInput], reference: str | None,
                 idempotency_key: str | None, receipt_date: date | None = None) -> CustomerReceipt:
    if idempotency_key:
        prev = db.execute(
            select(CustomerReceipt).where(CustomerReceipt.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if prev is not None:
            return prev
    customer = db.get(Party, customer_id)
    if customer is None or not customer.is_customer:
        raise NotFound("Cliente no encontrado.")
    if amount <= 0:
        raise ReceivablesError("El monto del cobro tiene que ser mayor a cero.")
    company = db.get(Company, 1)
    receipt = CustomerReceipt(
        id=uuid.uuid4(), number=next_number(db, "CUSTOMER_RECEIPT"), customer_id=customer.id,
        receipt_date=receipt_date or local_today(db), method=method,
        currency=company.base_currency if company else "PYG", amount=amount, unapplied_amount=amount,
        status=ReceiptStatus.POSTED, reference=reference, idempotency_key=idempotency_key,
        created_by_user_id=user_id,
    )
    db.add(receipt)
    _apply(db, receipt, allocations)
    return receipt


def _lock_receipt(db: Session, receipt_id: uuid.UUID) -> CustomerReceipt:
    receipt = db.execute(
        select(CustomerReceipt).where(CustomerReceipt.id == receipt_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if receipt is None:
        raise NotFound("Cobro no encontrado.")
    return receipt


def apply_receipt(db: Session, receipt_id: uuid.UUID, allocations: list[AllocationInput]) -> CustomerReceipt:
    """Aplica un anticipo (lo no aplicado de un cobro) a facturas."""
    receipt = _lock_receipt(db, receipt_id)
    if receipt.status != ReceiptStatus.POSTED:
        raise Conflict("El cobro esta anulado.")
    _apply(db, receipt, allocations)
    return receipt


def void_receipt(db: Session, receipt_id: uuid.UUID, reason: str) -> CustomerReceipt:
    receipt = _lock_receipt(db, receipt_id)
    if receipt.status == ReceiptStatus.VOIDED:
        return receipt
    invoices = _lock_invoices(db, [a.invoice_id for a in receipt.allocations]) if receipt.allocations else {}
    for a in receipt.allocations:
        inv = invoices[a.invoice_id]
        if inv.status == InvoiceStatus.ISSUED:
            inv.balance_due += a.amount
    receipt.status = ReceiptStatus.VOIDED
    receipt.unapplied_amount = Decimal("0")
    receipt.void_reason = reason
    receipt.voided_at = datetime.now(timezone.utc)
    return receipt


def credit_exposure(db: Session, customer_id: uuid.UUID) -> Decimal:
    """Deuda abierta + pedidos a credito confirmados/entregados sin factura
    vigente - anticipos sin aplicar."""
    open_invoices = db.execute(
        select(func.coalesce(func.sum(SalesInvoice.balance_due), 0))
        .where(SalesInvoice.customer_id == customer_id, SalesInvoice.status == InvoiceStatus.ISSUED)
    ).scalar_one()
    invoiced_orders = select(SalesInvoice.order_id).where(SalesInvoice.status == InvoiceStatus.ISSUED)
    pending_orders = db.execute(
        select(func.coalesce(func.sum(SalesOrder.total), 0)).where(
            SalesOrder.customer_id == customer_id,
            SalesOrder.payment_condition == PaymentCondition.CREDIT,
            SalesOrder.status.in_([SalesOrderStatus.CONFIRMED, SalesOrderStatus.DELIVERED]),
            SalesOrder.id.not_in(invoiced_orders),
        )
    ).scalar_one()
    advances = db.execute(
        select(func.coalesce(func.sum(CustomerReceipt.unapplied_amount), 0))
        .where(CustomerReceipt.customer_id == customer_id, CustomerReceipt.status == ReceiptStatus.POSTED)
    ).scalar_one()
    return Decimal(open_invoices) + Decimal(pending_orders) - Decimal(advances)


def assert_credit_available(db: Session, customer_id: uuid.UUID, amount: Decimal) -> None:
    # Lock del cliente: dos pedidos a credito simultaneos del mismo cliente
    # no pueden pasar el control los dos con el mismo margen disponible.
    customer = db.execute(
        select(Party).where(Party.id == customer_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one()
    if customer.credit_limit <= 0:
        raise Conflict("El cliente no tiene credito habilitado (limite 0).")
    exposure = credit_exposure(db, customer_id)
    if exposure + amount > customer.credit_limit:
        raise Conflict(
            f"Supera el limite de credito: limite {customer.credit_limit}, comprometido {exposure}, pedido {amount}."
        )


BUCKETS = ("current", "d1_30", "d31_60", "d61_90", "d90_plus")


def _bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "d1_30"
    if days_overdue <= 60:
        return "d31_60"
    if days_overdue <= 90:
        return "d61_90"
    return "d90_plus"


def aging(db: Session, as_of: date) -> list[dict]:
    rows = db.execute(
        select(SalesInvoice.customer_id, SalesInvoice.due_date, SalesInvoice.balance_due)
        .where(SalesInvoice.status == InvoiceStatus.ISSUED, SalesInvoice.balance_due > 0,
               SalesInvoice.issue_date <= as_of)
    ).all()
    advances = dict(db.execute(
        select(CustomerReceipt.customer_id, func.sum(CustomerReceipt.unapplied_amount))
        .where(CustomerReceipt.status == ReceiptStatus.POSTED, CustomerReceipt.unapplied_amount > 0,
               CustomerReceipt.receipt_date <= as_of)
        .group_by(CustomerReceipt.customer_id)
    ).all())
    by_customer: dict[uuid.UUID, dict] = {}
    for cid, due, bal in rows:
        entry = by_customer.setdefault(cid, {b: Decimal("0") for b in BUCKETS})
        entry[_bucket((as_of - due).days)] += bal
    for cid in advances:
        by_customer.setdefault(cid, {b: Decimal("0") for b in BUCKETS})
    names = dict(db.execute(select(Party.id, Party.legal_name).where(Party.id.in_(list(by_customer)))).all()) if by_customer else {}
    result = []
    for cid, buckets in by_customer.items():
        total = sum(buckets.values())
        adv = Decimal(advances.get(cid, 0))
        result.append({"customer_id": cid, "customer_name": names.get(cid, ""), **buckets,
                       "total_due": total, "unapplied_advances": adv, "net_balance": total - adv})
    return sorted(result, key=lambda r: r["net_balance"], reverse=True)


def statement(db: Session, customer_id: uuid.UUID) -> list[dict]:
    events = []
    for inv in db.execute(select(SalesInvoice).where(SalesInvoice.customer_id == customer_id)).scalars():
        events.append({"date": inv.issue_date, "created_at": inv.created_at, "kind": "INVOICE", "number": inv.number,
                       "debit": inv.total, "credit": Decimal("0"), "voided": inv.status == InvoiceStatus.VOIDED})
    for rc in db.execute(select(CustomerReceipt).where(CustomerReceipt.customer_id == customer_id)).scalars():
        events.append({"date": rc.receipt_date, "created_at": rc.created_at, "kind": "RECEIPT", "number": rc.number,
                       "debit": Decimal("0"), "credit": rc.amount, "voided": rc.status == ReceiptStatus.VOIDED})
    events.sort(key=lambda e: (e["date"], e["created_at"]))
    running = Decimal("0")
    for e in events:
        if not e["voided"]:
            running += e["debit"] - e["credit"]
        e["balance"] = running
        del e["created_at"]
    return events

