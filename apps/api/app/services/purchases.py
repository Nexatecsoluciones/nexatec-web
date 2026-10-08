"""Compras y cuentas por pagar.

OC: DRAFT -> CONFIRMED -> PARTIALLY_RECEIVED -> RECEIVED; CANCELLED si no se
recibio nada; CLOSED para cortar una OC recibida en parte.

Recepcion: entra stock al costo NETO de IVA de la linea (line_net / cantidad).
Idempotencia de la recepcion: la clave se guarda en cada movimiento como
"<clave>:<linea>"; si ya hay movimientos con esa clave para la OC, el
reintento no vuelve a recibir. (Una recepcion que solo tenga servicios no
genera movimientos y por lo tanto no es idempotente -- documentado.)

Facturas de proveedor: se REGISTRAN (son de terceros). Control de duplicado
en la base, IVA consistente con lo gravado, y si estan ligadas a una OC no
pueden superar lo efectivamente recibido.

Nada hace commit: una request = una transaccion."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services import inventory
from app.services.receivables import BUCKETS, AllocationInput, Conflict, ReceivablesError, _bucket, local_today
from app.services.sales import NotFound, _currency, _current_tax_rate, compute_line, next_number
from app.tenant_models.core import Company, Party, Product, ProductType, Warehouse
from app.tenant_models.inventory import StockMovement
from app.tenant_models.purchases import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    SupplierInvoice,
    SupplierPayment,
    SupplierPaymentAllocation,
)
from app.tenant_models.receivables import InvoiceStatus, PaymentMethod, ReceiptStatus

PurchasesError = ReceivablesError


@dataclass
class POLineInput:
    product_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    description: str | None = None


@dataclass
class ReceiveItem:
    line_no: int
    quantity: Decimal


@dataclass
class Breakdown:
    taxable_10: Decimal = Decimal("0")
    vat_10: Decimal = Decimal("0")
    taxable_5: Decimal = Decimal("0")
    vat_5: Decimal = Decimal("0")
    exempt: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.taxable_10 + self.vat_10 + self.taxable_5 + self.vat_5 + self.exempt


# --- Ordenes de compra ---------------------------------------------------------------


def _build_lines(db: Session, po: PurchaseOrder, lines: list[POLineInput], decimals: int) -> None:
    if not lines:
        raise PurchasesError("La orden tiene que tener al menos una linea.")
    po.lines.clear()
    db.flush()
    net = tax = total = Decimal(0)
    for i, li in enumerate(lines, start=1):
        product = db.get(Product, li.product_id)
        if product is None or not product.is_active:
            raise PurchasesError("Producto inexistente o inactivo.")
        if li.quantity <= 0 or li.unit_price < 0:
            raise PurchasesError("Cantidad y precio invalidos.")
        rate = _current_tax_rate(db, product.tax_code)
        l_net, l_tax, l_total = compute_line(li.quantity, li.unit_price, Decimal(0), rate, decimals)
        po.lines.append(PurchaseOrderLine(
            line_no=i, product_id=product.id, description=li.description or product.name, quantity=li.quantity,
            quantity_received=Decimal(0), unit_price=li.unit_price, tax_code=product.tax_code, tax_rate=rate,
            line_net=l_net, line_tax=l_tax, line_total=l_total,
        ))
        net += l_net
        tax += l_tax
        total += l_total
    po.subtotal_net, po.tax_total, po.total = net, tax, total


def _supplier(db: Session, supplier_id: uuid.UUID) -> Party:
    supplier = db.get(Party, supplier_id)
    if supplier is None or not supplier.is_supplier or not supplier.is_active:
        raise PurchasesError("El proveedor no existe, no es proveedor o esta inactivo.")
    return supplier


def create_po(db: Session, *, user_id, supplier_id, warehouse_id, lines: list[POLineInput],
              expected_date: date | None, notes: str | None) -> PurchaseOrder:
    _supplier(db, supplier_id)
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None or not warehouse.is_active:
        raise PurchasesError("Deposito inexistente o inactivo.")
    currency = _currency(db)
    po = PurchaseOrder(
        id=uuid.uuid4(), number=next_number(db, "PURCHASE_ORDER"), supplier_id=supplier_id, warehouse_id=warehouse_id,
        status=PurchaseOrderStatus.DRAFT, currency=currency.code, expected_date=expected_date, notes=notes,
        subtotal_net=0, tax_total=0, total=0, created_by_user_id=user_id,
    )
    db.add(po)
    _build_lines(db, po, lines, currency.decimals)
    return po


def _lock_po(db: Session, po_id: uuid.UUID) -> PurchaseOrder:
    po = db.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if po is None:
        raise NotFound("Orden de compra no encontrada.")
    return po


def replace_po_lines(db: Session, po_id: uuid.UUID, lines: list[POLineInput]) -> PurchaseOrder:
    po = _lock_po(db, po_id)
    if po.status != PurchaseOrderStatus.DRAFT:
        raise Conflict("Solo se editan ordenes en borrador.")
    _build_lines(db, po, lines, _currency(db).decimals)
    return po


def confirm_po(db: Session, po_id: uuid.UUID) -> PurchaseOrder:
    po = _lock_po(db, po_id)
    if po.status == PurchaseOrderStatus.CONFIRMED:
        return po
    if po.status != PurchaseOrderStatus.DRAFT:
        raise Conflict(f"No se puede confirmar una orden {po.status.value}.")
    po.status = PurchaseOrderStatus.CONFIRMED
    po.confirmed_at = datetime.now(timezone.utc)
    return po


def receive(db: Session, po_id: uuid.UUID, user_id, items: list[ReceiveItem], idempotency_key: str | None) -> PurchaseOrder:
    po = _lock_po(db, po_id)
    if idempotency_key:
        done = db.execute(
            select(StockMovement.id).where(StockMovement.reference == po.number,
                                           StockMovement.idempotency_key.like(f"{idempotency_key}:%")).limit(1)
        ).first()
        if done is not None:
            return po
    if po.status not in (PurchaseOrderStatus.CONFIRMED, PurchaseOrderStatus.PARTIALLY_RECEIVED):
        raise Conflict(f"No se puede recibir una orden {po.status.value}.")
    if not items:
        raise PurchasesError("No hay nada para recibir.")
    by_no = {ln.line_no: ln for ln in po.lines}
    # Orden fijo por producto: mismo criterio de locks que el resto del inventario.
    for item in sorted(items, key=lambda it: (str(by_no[it.line_no].product_id) if it.line_no in by_no else "", it.line_no)):
        line = by_no.get(item.line_no)
        if line is None:
            raise NotFound(f"La linea {item.line_no} no existe en la orden.")
        if item.quantity <= 0:
            raise PurchasesError("La cantidad recibida tiene que ser mayor a cero.")
        if line.quantity_received + item.quantity > line.quantity:
            raise Conflict(f"La linea {line.line_no} recibiria mas de lo pedido ({line.quantity}).")
        product = db.get(Product, line.product_id)
        if product.product_type == ProductType.GOOD and product.tracks_stock:
            unit_cost = (line.line_net / line.quantity).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            op = inventory.OpContext(
                db=db, user_id=user_id, reference=po.number,
                idempotency_key=f"{idempotency_key}:{line.line_no}" if idempotency_key else None,
            )
            inventory.receive(op, line.product_id, po.warehouse_id, item.quantity, unit_cost)
        line.quantity_received += item.quantity
    complete = all(ln.quantity_received == ln.quantity for ln in po.lines)
    po.status = PurchaseOrderStatus.RECEIVED if complete else PurchaseOrderStatus.PARTIALLY_RECEIVED
    return po


def cancel_or_close_po(db: Session, po_id: uuid.UUID, reason: str) -> PurchaseOrder:
    po = _lock_po(db, po_id)
    if po.status in (PurchaseOrderStatus.CANCELLED, PurchaseOrderStatus.CLOSED):
        return po
    if po.status == PurchaseOrderStatus.RECEIVED:
        raise Conflict("La orden ya fue recibida completa.")
    received_any = any(ln.quantity_received > 0 for ln in po.lines)
    po.status = PurchaseOrderStatus.CLOSED if received_any else PurchaseOrderStatus.CANCELLED
    po.close_reason = reason
    return po


def received_value(po: PurchaseOrder, decimals: int) -> Decimal:
    q = Decimal(1).scaleb(-decimals)
    return sum(
        ((ln.line_total * ln.quantity_received / ln.quantity).quantize(q, rounding=ROUND_HALF_UP) for ln in po.lines),
        Decimal(0),
    )


# --- Facturas de proveedor -------------------------------------------------------------


def _check_vat(taxable: Decimal, vat: Decimal, rate: Decimal, unit: Decimal) -> None:
    expected = (taxable * rate / 100).quantize(unit, rounding=ROUND_HALF_UP)
    if abs(expected - vat) > unit:
        raise PurchasesError(f"El IVA {rate}% ({vat}) no coincide con lo gravado ({taxable} -> {expected}).")


def register_supplier_invoice(db: Session, *, user_id, supplier_id, supplier_invoice_number: str,
                              supplier_timbrado: str | None, issue_date: date, due_date: date | None,
                              breakdown: Breakdown, purchase_order_id: uuid.UUID | None) -> SupplierInvoice:
    supplier = _supplier(db, supplier_id)
    currency = _currency(db)
    unit = Decimal(1).scaleb(-currency.decimals)
    total = breakdown.total
    if total <= 0:
        raise PurchasesError("El total de la factura tiene que ser mayor a cero.")
    _check_vat(breakdown.taxable_10, breakdown.vat_10, Decimal(10), unit)
    _check_vat(breakdown.taxable_5, breakdown.vat_5, Decimal(5), unit)

    if purchase_order_id is not None:
        po = _lock_po(db, purchase_order_id)
        if po.supplier_id != supplier.id:
            raise PurchasesError("La orden de compra es de otro proveedor.")
        if po.status in (PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.CANCELLED):
            raise Conflict("La orden de compra no esta vigente.")
        already = db.execute(
            select(func.coalesce(func.sum(SupplierInvoice.total), 0)).where(
                SupplierInvoice.purchase_order_id == po.id, SupplierInvoice.status == InvoiceStatus.ISSUED)
        ).scalar_one()
        # Tolerancia: una unidad de moneda por linea (redondeos del proveedor).
        tolerance = unit * len(po.lines)
        if Decimal(already) + total > received_value(po, currency.decimals) + tolerance:
            raise Conflict("La factura supera lo recibido de la orden de compra (control factura vs recepcion).")

    due = due_date or issue_date + timedelta(days=supplier.payment_terms_days)
    if due < issue_date:
        raise PurchasesError("El vencimiento no puede ser anterior a la emision.")
    invoice = SupplierInvoice(
        id=uuid.uuid4(), supplier_id=supplier.id, supplier_invoice_number=supplier_invoice_number.strip(),
        supplier_timbrado=supplier_timbrado, purchase_order_id=purchase_order_id, status=InvoiceStatus.ISSUED,
        issue_date=issue_date, due_date=due, currency=currency.code, total=total, balance_due=total,
        taxable_10=breakdown.taxable_10, vat_10=breakdown.vat_10, taxable_5=breakdown.taxable_5,
        vat_5=breakdown.vat_5, exempt=breakdown.exempt, created_by_user_id=user_id,
    )
    db.add(invoice)
    return invoice


def _lock_supplier_invoices(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, SupplierInvoice]:
    rows = db.execute(
        select(SupplierInvoice).where(SupplierInvoice.id.in_(ids)).order_by(SupplierInvoice.id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalars().all()
    return {inv.id: inv for inv in rows}


def void_supplier_invoice(db: Session, invoice_id: uuid.UUID, reason: str) -> SupplierInvoice:
    inv = _lock_supplier_invoices(db, [invoice_id]).get(invoice_id)
    if inv is None:
        raise NotFound("Factura de proveedor no encontrada.")
    if inv.status == InvoiceStatus.VOIDED:
        return inv
    if inv.balance_due != inv.total:
        raise Conflict("La factura tiene pagos aplicados: anular primero esos pagos.")
    inv.status = InvoiceStatus.VOIDED
    inv.balance_due = Decimal(0)
    inv.void_reason = reason
    inv.voided_at = datetime.now(timezone.utc)
    return inv


# --- Pagos a proveedores ---------------------------------------------------------------


def _apply(db: Session, payment: SupplierPayment, allocations: list[AllocationInput]) -> None:
    if not allocations:
        return
    ids = [a.invoice_id for a in allocations]
    if len(set(ids)) != len(ids):
        raise PurchasesError("Una factura aparece dos veces en la aplicacion.")
    invoices = _lock_supplier_invoices(db, ids)
    total = Decimal(0)
    for a in allocations:
        if a.amount <= 0:
            raise PurchasesError("Cada aplicacion tiene que ser mayor a cero.")
        inv = invoices.get(a.invoice_id)
        if inv is None or inv.supplier_id != payment.supplier_id:
            raise NotFound("Factura no encontrada para este proveedor.")
        if inv.status != InvoiceStatus.ISSUED:
            raise Conflict(f"La factura {inv.supplier_invoice_number} no esta vigente.")
        if a.amount > inv.balance_due:
            raise Conflict(f"El pago supera el saldo de la factura {inv.supplier_invoice_number} ({inv.balance_due}).")
        total += a.amount
    if total > payment.unapplied_amount:
        raise Conflict("Lo aplicado supera el monto disponible del pago.")
    for a in allocations:
        invoices[a.invoice_id].balance_due -= a.amount
        payment.allocations.append(SupplierPaymentAllocation(invoice_id=a.invoice_id, amount=a.amount))
    payment.unapplied_amount -= total


def post_payment(db: Session, *, user_id, supplier_id, method: PaymentMethod, amount: Decimal,
                 allocations: list[AllocationInput], reference: str | None, idempotency_key: str | None) -> SupplierPayment:
    if idempotency_key:
        prev = db.execute(select(SupplierPayment).where(SupplierPayment.idempotency_key == idempotency_key)).scalar_one_or_none()
        if prev is not None:
            return prev
    supplier = db.get(Party, supplier_id)
    if supplier is None or not supplier.is_supplier:
        raise NotFound("Proveedor no encontrado.")
    if amount <= 0:
        raise PurchasesError("El monto del pago tiene que ser mayor a cero.")
    company = db.get(Company, 1)
    payment = SupplierPayment(
        id=uuid.uuid4(), number=next_number(db, "SUPPLIER_PAYMENT"), supplier_id=supplier.id,
        payment_date=local_today(db), method=method, currency=company.base_currency if company else "PYG",
        amount=amount, unapplied_amount=amount, status=ReceiptStatus.POSTED, reference=reference,
        idempotency_key=idempotency_key, created_by_user_id=user_id,
    )
    db.add(payment)
    _apply(db, payment, allocations)
    return payment


def _lock_payment(db: Session, payment_id: uuid.UUID) -> SupplierPayment:
    p = db.execute(
        select(SupplierPayment).where(SupplierPayment.id == payment_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if p is None:
        raise NotFound("Pago no encontrado.")
    return p


def apply_payment(db: Session, payment_id: uuid.UUID, allocations: list[AllocationInput]) -> SupplierPayment:
    payment = _lock_payment(db, payment_id)
    if payment.status != ReceiptStatus.POSTED:
        raise Conflict("El pago esta anulado.")
    _apply(db, payment, allocations)
    return payment


def void_payment(db: Session, payment_id: uuid.UUID, reason: str) -> SupplierPayment:
    payment = _lock_payment(db, payment_id)
    if payment.status == ReceiptStatus.VOIDED:
        return payment
    invoices = _lock_supplier_invoices(db, [a.invoice_id for a in payment.allocations]) if payment.allocations else {}
    for a in payment.allocations:
        if invoices[a.invoice_id].status == InvoiceStatus.ISSUED:
            invoices[a.invoice_id].balance_due += a.amount
    payment.status = ReceiptStatus.VOIDED
    payment.unapplied_amount = Decimal(0)
    payment.void_reason = reason
    payment.voided_at = datetime.now(timezone.utc)
    return payment


def ap_aging(db: Session, as_of: date) -> list[dict]:
    rows = db.execute(
        select(SupplierInvoice.supplier_id, SupplierInvoice.due_date, SupplierInvoice.balance_due)
        .where(SupplierInvoice.status == InvoiceStatus.ISSUED, SupplierInvoice.balance_due > 0,
               SupplierInvoice.issue_date <= as_of)
    ).all()
    advances = dict(db.execute(
        select(SupplierPayment.supplier_id, func.sum(SupplierPayment.unapplied_amount))
        .where(SupplierPayment.status == ReceiptStatus.POSTED, SupplierPayment.unapplied_amount > 0,
               SupplierPayment.payment_date <= as_of)
        .group_by(SupplierPayment.supplier_id)
    ).all())
    by_supplier: dict[uuid.UUID, dict] = {}
    for sid, due, bal in rows:
        entry = by_supplier.setdefault(sid, {b: Decimal(0) for b in BUCKETS})
        entry[_bucket((as_of - due).days)] += bal
    for sid in advances:
        by_supplier.setdefault(sid, {b: Decimal(0) for b in BUCKETS})
    names = dict(db.execute(select(Party.id, Party.legal_name).where(Party.id.in_(list(by_supplier)))).all()) if by_supplier else {}
    result = []
    for sid, buckets in by_supplier.items():
        total = sum(buckets.values())
        adv = Decimal(advances.get(sid, 0))
        result.append({"supplier_id": sid, "supplier_name": names.get(sid, ""), **buckets,
                       "total_due": total, "unapplied_advances": adv, "net_balance": total - adv})
    return sorted(result, key=lambda r: r["net_balance"], reverse=True)
