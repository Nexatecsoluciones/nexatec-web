"""Venta rapida, caja y codigos de barras.

Una venta rapida reutiliza el motor existente, en la MISMA transaccion:
sales.create_order -> confirm (reserva) -> deliver (kardex + costo) ->
receivables.invoice_order (factura interna + asiento) -> post_receipt por
cada medio de pago (asiento de cobro). Lo unico propio es el registro del
mostrador (PosSale), los movimientos de caja y el vuelto. Si algo falla,
el router hace rollback de todo: no queda ni pedido, ni stock, ni cobro.

Idempotencia: la clave del cliente (Idempotency-Key) es unica en pos_sales;
un reintento (doble clic, red) devuelve la venta ya hecha.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.services import accounting, receivables, sales
from app.services.receivables import AllocationInput, CreditLineInput, local_today
from app.services.sales import InvalidTransition, SalesError, _currency, _q, next_number
from app.tenant_models.core import Company, Party, Product
from app.tenant_models.inventory import StockBalance
from app.tenant_models.pos import (
    CashMovement, CashMovementKind, CashRegister, CashSession, PosSale, PosSaleStatus, ProductBarcode, SessionStatus,
    Symbology,
)
from app.tenant_models.receivables import CreditNoteKind, CustomerReceipt, PaymentMethod
from app.tenant_models.sales import PaymentCondition, SalesOrderLine

D0 = Decimal("0")


# --- Codigos de barras ----------------------------------------------------------------


def _gtin_ok(code: str) -> bool:
    """Digito verificador GS1 (EAN-8, UPC-A, EAN-13): pesos 3/1 desde la derecha."""
    if not code.isdigit():
        return False
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def classify(code: str) -> Symbology:
    code = code.strip()
    if code.isdigit() and len(code) in (8, 12, 13):
        if not _gtin_ok(code):
            raise SalesError(f"El codigo {code} no es un EAN/UPC valido (digito verificador incorrecto).")
        return {8: Symbology.EAN8, 12: Symbology.UPCA, 13: Symbology.EAN13}[len(code)]
    if not 1 <= len(code) <= 48 or any(ord(c) < 32 or ord(c) > 126 for c in code):
        raise SalesError("Codigo invalido: 1 a 48 caracteres imprimibles (Code 128).")
    return Symbology.CODE128


def add_barcode(db: Session, product_id, code: str, primary: bool) -> ProductBarcode:
    product = db.get(Product, product_id)
    if product is None:
        raise SalesError("Producto inexistente.")
    code = code.strip()
    sym = classify(code)
    if db.execute(select(ProductBarcode).where(ProductBarcode.code == code)).scalar_one_or_none():
        raise InvalidTransition(f"El codigo {code} ya esta asignado a otro producto.")
    return _attach(db, product_id, code, sym, primary, internal=False)


def generate_internal(db: Session, product_id, primary: bool = True) -> ProductBarcode:
    """Codigo interno Code 128 ('NX000123'): no es un GTIN, no se registra en
    GS1 y no se confunde con uno (letras + digitos)."""
    if db.get(Product, product_id) is None:
        raise SalesError("Producto inexistente.")
    code = next_number(db, "BARCODE_INTERNAL")
    return _attach(db, product_id, code, Symbology.CODE128, primary, internal=True)


def _attach(db: Session, product_id, code, sym, primary, internal) -> ProductBarcode:
    has_primary = db.execute(select(ProductBarcode).where(ProductBarcode.product_id == product_id,
                                                          ProductBarcode.is_primary)).scalar_one_or_none()
    if primary and has_primary is not None:
        has_primary.is_primary = False
        db.flush()
    bc = ProductBarcode(id=uuid.uuid4(), product_id=product_id, code=code, symbology=sym,
                        is_primary=primary or has_primary is None, is_internal=internal)
    db.add(bc)
    db.flush()
    return bc


def lookup(db: Session, code: str, warehouse_id=None) -> dict | None:
    """Codigo de barras exacto, o SKU exacto (sin distinguir mayusculas)."""
    code = code.strip()
    product = db.execute(select(Product).join(ProductBarcode, ProductBarcode.product_id == Product.id)
                         .where(ProductBarcode.code == code)).scalar_one_or_none()
    if product is None:
        product = db.execute(select(Product).where(func.upper(Product.sku) == code.upper())).scalar_one_or_none()
    if product is None or not product.is_active:
        return None
    return product_card(db, product, warehouse_id)


def product_card(db: Session, product: Product, warehouse_id) -> dict:
    available = None
    if warehouse_id and product.tracks_stock:
        bal = db.get(StockBalance, (product.id, warehouse_id))
        available = (bal.on_hand - bal.reserved) if bal else D0
    return {"product_id": product.id, "sku": product.sku, "name": product.name, "sale_price": product.sale_price,
            "tax_code": product.tax_code, "tracks_stock": product.tracks_stock, "available": available}


# --- Caja -----------------------------------------------------------------------------


def _session(db: Session, session_id, lock=True) -> CashSession:
    stmt = select(CashSession).where(CashSession.id == session_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    s = db.execute(stmt).scalar_one_or_none()
    if s is None:
        raise SalesError("Turno de caja inexistente.")
    return s


def my_open_session(db: Session, user_id) -> CashSession | None:
    return db.execute(select(CashSession).where(CashSession.opened_by_user_id == user_id,
                                                CashSession.status == SessionStatus.OPEN)).scalar_one_or_none()


def open_session(db: Session, register_id, user_id, opening_float: Decimal) -> CashSession:
    reg = db.get(CashRegister, register_id)
    if reg is None or not reg.is_active:
        raise SalesError("Caja inexistente o inactiva.")
    if my_open_session(db, user_id) is not None:
        raise InvalidTransition("Ya tenes un turno de caja abierto: cerralo antes de abrir otro.")
    busy = db.execute(select(CashSession).where(CashSession.register_id == register_id,
                                                CashSession.status == SessionStatus.OPEN)).scalar_one_or_none()
    if busy is not None:
        raise InvalidTransition(f"La caja {reg.code} ya esta abierta (turno {busy.number}).")
    s = CashSession(id=uuid.uuid4(), number=next_number(db, "CASH_SESSION"), register_id=reg.id,
                    status=SessionStatus.OPEN, opened_by_user_id=user_id, opening_float=opening_float)
    db.add(s)
    db.flush()
    return s


def expected_cash(db: Session, session: CashSession) -> Decimal:
    moved = db.execute(select(func.coalesce(func.sum(CashMovement.amount * CashMovement.direction), 0)).where(
        CashMovement.session_id == session.id, CashMovement.method == PaymentMethod.CASH)).scalar_one()
    return Decimal(session.opening_float) + Decimal(moved)


def summary(db: Session, session: CashSession) -> dict:
    by_method: dict[str, Decimal] = {}
    by_kind: dict[str, Decimal] = {}
    for kind, method, direction, amount in db.execute(select(
            CashMovement.kind, CashMovement.method, CashMovement.direction, func.sum(CashMovement.amount))
            .where(CashMovement.session_id == session.id)
            .group_by(CashMovement.kind, CashMovement.method, CashMovement.direction)):
        signed = Decimal(amount) * direction
        by_method[method.value] = by_method.get(method.value, D0) + signed
        by_kind[kind.value] = by_kind.get(kind.value, D0) + signed
    sales_count, sales_total = db.execute(select(func.count(), func.coalesce(func.sum(PosSale.total), 0)).where(
        PosSale.session_id == session.id, PosSale.status == PosSaleStatus.COMPLETED)).one()
    voided = db.execute(select(func.count()).select_from(PosSale).where(
        PosSale.session_id == session.id, PosSale.status == PosSaleStatus.VOIDED)).scalar_one()
    return {"expected_cash": expected_cash(db, session), "by_method": by_method, "by_kind": by_kind,
            "sales_count": sales_count, "sales_total": Decimal(sales_total), "voided_count": voided}


_MOVE = {
    # kind: (direccion, cuenta contrapartida)
    CashMovementKind.CASH_IN: (1, "OTHER_INCOME"),
    CashMovementKind.FROM_BANK: (1, "BANK"),
    CashMovementKind.EXPENSE: (-1, "EXPENSE_DEFAULT"),
    CashMovementKind.TO_BANK: (-1, "BANK"),
}


def manual_movement(db: Session, *, session_id, user_id, kind: CashMovementKind, amount: Decimal,
                    description: str) -> CashMovement:
    if kind not in _MOVE:
        raise SalesError("Tipo de movimiento no permitido a mano.")
    s = _session(db, session_id)
    if s.status != SessionStatus.OPEN:
        raise InvalidTransition("El turno esta cerrado.")
    direction, counter = _MOVE[kind]
    if direction < 0 and amount > expected_cash(db, s):
        raise InvalidTransition("No hay tanto efectivo en la caja.")
    m = CashMovement(id=uuid.uuid4(), session_id=s.id, kind=kind, method=PaymentMethod.CASH, direction=direction,
                     amount=amount, description=description, created_by_user_id=user_id)
    db.add(m)
    db.flush()
    cash, other = accounting.account_for(db, "CASH"), accounting.account_for(db, counter)
    lines = ([accounting.Line(cash, debit=amount), accounting.Line(other, credit=amount)] if direction > 0
             else [accounting.Line(other, debit=amount), accounting.Line(cash, credit=amount)])
    accounting.post_entry(db, accounting.EntryDraft(local_today(db), f"Caja {s.number}: {description}",
                                                    "CASH_MOVEMENT", m.id, lines), user_id)
    return m


def close_session(db: Session, *, session_id, user_id, counted_cash: Decimal, count_detail: dict | None,
                  notes: str | None, can_close_others: bool) -> CashSession:
    s = _session(db, session_id)
    if s.status != SessionStatus.OPEN:
        raise InvalidTransition("El turno ya esta cerrado.")
    if s.opened_by_user_id != user_id and not can_close_others:
        raise InvalidTransition("Solo quien abrio el turno (o un supervisor) puede cerrarlo.")
    if count_detail:
        total = sum(Decimal(k) * int(v) for k, v in count_detail.items())
        if total != counted_cash:
            raise SalesError(f"El detalle de billetes suma {total}, no {counted_cash}.")
    expected = expected_cash(db, s)
    diff = counted_cash - expected
    if diff != 0:
        cash = accounting.account_for(db, "CASH")
        if diff > 0:
            lines = [accounting.Line(cash, debit=diff), accounting.Line(accounting.account_for(db, "CASH_OVER"), credit=diff)]
        else:
            lines = [accounting.Line(accounting.account_for(db, "CASH_SHORT"), debit=-diff), accounting.Line(cash, credit=-diff)]
        accounting.post_entry(db, accounting.EntryDraft(local_today(db), f"Diferencia de arqueo {s.number}",
                                                        "CASH_SESSION_CLOSE", s.id, lines), user_id)
    s.status = SessionStatus.CLOSED
    s.closed_at = datetime.now(timezone.utc)
    s.closed_by_user_id = user_id
    s.expected_cash, s.counted_cash, s.difference = expected, counted_cash, diff
    s.count_detail = count_detail
    s.closing_notes = notes
    return s


# --- Venta rapida ---------------------------------------------------------------------


@dataclass
class PosLine:
    product_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal | None = None
    discount_pct: Decimal = D0


@dataclass
class PosPayment:
    method: PaymentMethod
    amount: Decimal
    reference: str | None = None


@dataclass
class SaleResult:
    sale: PosSale
    replayed: bool = False
    warnings: list[str] = field(default_factory=list)


def default_customer(db: Session, register: CashRegister) -> uuid.UUID:
    if register.default_customer_id:
        return register.default_customer_id
    party = db.execute(select(Party).where(Party.legal_name == "Consumidor final", Party.is_customer.is_(True))
                       ).scalars().first()
    if party is None:
        party = Party(id=uuid.uuid4(), legal_name="Consumidor final", is_customer=True, is_supplier=False)
        db.add(party)
        db.flush()
    register.default_customer_id = party.id
    return party.id


def sell(db: Session, *, user_id, session_id, customer_id, lines: list[PosLine], condition: PaymentCondition,
         payments: list[PosPayment], idempotency_key: str, can_discount: bool) -> SaleResult:
    prev = db.execute(select(PosSale).where(PosSale.idempotency_key == idempotency_key)).scalar_one_or_none()
    if prev is not None:
        return SaleResult(prev, replayed=True)
    s = _session(db, session_id)
    if s.status != SessionStatus.OPEN:
        raise InvalidTransition("El turno de caja esta cerrado: abri uno para vender.")
    if s.opened_by_user_id != user_id:
        raise InvalidTransition("Ese turno de caja es de otro usuario.")
    if not lines:
        raise SalesError("El carrito esta vacio.")
    for ln in lines:
        if ln.quantity <= 0:
            raise SalesError("Las cantidades tienen que ser mayores a cero.")
        if not can_discount:
            product = db.get(Product, ln.product_id)
            if ln.discount_pct != 0 or (ln.unit_price is not None and product and ln.unit_price != product.sale_price):
                raise InvalidTransition("Tu rol no puede cambiar precios ni hacer descuentos.")
    register = db.get(CashRegister, s.register_id)
    customer_id = customer_id or default_customer(db, register)

    order = sales.create_order(
        db, user_id=user_id, customer_id=customer_id, warehouse_id=register.warehouse_id, payment_condition=condition,
        lines=[sales.LineInput(product_id=ln.product_id, quantity=ln.quantity, unit_price=ln.unit_price,
                               discount_pct=ln.discount_pct) for ln in lines],
        notes=f"Venta rapida - caja {register.code}, turno {s.number}")
    # Las sesiones de tenant no hacen autoflush y cada paso relee el pedido con
    # populate_existing: flush explicito entre pasos.
    db.flush()
    sales.confirm(db, order.id, user_id)
    db.flush()
    sales.deliver(db, order.id, user_id)
    db.flush()
    invoice = receivables.invoice_order(db, order.id, user_id)
    db.flush()
    total = invoice.total

    # Medios de pago: lo no-efectivo nunca supera el total; el efectivo puede
    # superarlo (vuelto). Contado = pagado completo; credito = entrega opcional.
    q = _q(_currency(db).decimals)
    if any(p.amount <= 0 for p in payments):
        raise SalesError("Cada pago tiene que ser mayor a cero.")
    non_cash = sum((p.amount for p in payments if p.method != PaymentMethod.CASH), D0)
    tendered = sum((p.amount for p in payments if p.method == PaymentMethod.CASH), D0)
    if non_cash > total:
        raise SalesError("Los pagos con tarjeta/transferencia superan el total.")
    cash_needed = total - non_cash
    cash_applied = min(tendered, cash_needed)
    change = (tendered - cash_applied).quantize(q, ROUND_HALF_UP)
    paid = non_cash + cash_applied
    if condition == PaymentCondition.CASH and paid < total:
        raise SalesError(f"Falta cobrar {total - paid} para una venta de contado.")
    if condition == PaymentCondition.CREDIT and change > 0:
        raise SalesError("En una venta a credito no se da vuelto: la entrega no puede superar el total.")

    sale = PosSale(id=uuid.uuid4(), session_id=s.id, order_id=order.id, invoice_id=invoice.id, customer_id=customer_id,
                   status=PosSaleStatus.COMPLETED, total=total, paid=paid, tendered_cash=tendered, change_given=change,
                   payments=[{"method": p.method.value, "amount": str(p.amount), "reference": p.reference} for p in payments],
                   idempotency_key=idempotency_key, created_by_user_id=user_id)
    db.add(sale)
    db.flush()

    to_apply = [(p.method, p.amount, p.reference) for p in payments if p.method != PaymentMethod.CASH]
    if cash_applied > 0:
        to_apply.append((PaymentMethod.CASH, cash_applied, None))
    for i, (method, amount, ref) in enumerate(to_apply):
        rc = receivables.post_receipt(db, user_id=user_id, customer_id=customer_id, method=method, amount=amount,
                                      allocations=[AllocationInput(invoice.id, amount)], reference=ref or invoice.number,
                                      idempotency_key=f"pos:{idempotency_key}:{i}")
        db.flush()
        db.add(CashMovement(id=uuid.uuid4(), session_id=s.id, kind=CashMovementKind.SALE, method=method, direction=1,
                            amount=amount, receipt_id=rc.id, pos_sale_id=sale.id, description=f"Venta {invoice.number}",
                            created_by_user_id=user_id))
    db.flush()
    return SaleResult(sale)


def void_sale(db: Session, *, sale_id, user_id, reason: str) -> PosSale:
    """Anulacion completa: anula los cobros (revierte caja y asiento) y emite
    una nota de credito por devolucion total con reingreso de stock. Solo
    mientras el turno de la venta siga abierto: despues de un arqueo, el
    dinero ya se conto y corresponde una devolucion con nota de credito +
    egreso de caja."""
    sale = db.execute(select(PosSale).where(PosSale.id == sale_id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one_or_none()
    if sale is None:
        raise SalesError("Venta inexistente.")
    if sale.status == PosSaleStatus.VOIDED:
        return sale
    s = _session(db, sale.session_id)
    if s.status != SessionStatus.OPEN:
        raise InvalidTransition("El turno de esa venta ya se cerro: hace una nota de credito y un egreso de caja.")
    moves = list(db.execute(select(CashMovement).where(CashMovement.pos_sale_id == sale.id,
                                                       CashMovement.kind == CashMovementKind.SALE)).scalars())
    for m in moves:
        receivables.void_receipt(db, m.receipt_id, f"Anulacion de venta rapida: {reason}")
        db.flush()
        db.add(CashMovement(id=uuid.uuid4(), session_id=s.id, kind=CashMovementKind.VOID, method=m.method, direction=-1,
                            amount=m.amount, receipt_id=m.receipt_id, pos_sale_id=sale.id,
                            description=f"Anulacion: {reason}", created_by_user_id=user_id))
    db.flush()
    lines = list(db.execute(select(SalesOrderLine).where(SalesOrderLine.order_id == sale.order_id)).scalars())
    note = receivables.issue_credit_note(
        db, invoice_id=sale.invoice_id, kind=CreditNoteKind.RETURN, reason=f"Anulacion de venta rapida: {reason}",
        restock=True, lines=[CreditLineInput(line_no=ln.line_no, quantity=ln.quantity - ln.quantity_returned)
                             for ln in lines if ln.quantity > ln.quantity_returned], user_id=user_id)
    sale.status = PosSaleStatus.VOIDED
    sale.voided_at = datetime.now(timezone.utc)
    sale.voided_by_user_id = user_id
    sale.void_reason = reason
    sale.credit_note_id = note.id
    return sale


def collect_balance(db: Session, *, session_id, user_id, customer_id, method: PaymentMethod, amount: Decimal,
                    idempotency_key: str) -> CustomerReceipt:
    """Cobro de saldo en caja: aplica a las facturas mas viejas primero; lo
    que sobra queda como anticipo del cliente."""
    from app.tenant_models.receivables import InvoiceStatus, SalesInvoice

    s = _session(db, session_id)
    if s.status != SessionStatus.OPEN or s.opened_by_user_id != user_id:
        raise InvalidTransition("Necesitas tu turno de caja abierto para cobrar.")
    remaining, allocs = amount, []
    for inv in db.execute(select(SalesInvoice).where(SalesInvoice.customer_id == customer_id,
                                                     SalesInvoice.status == InvoiceStatus.ISSUED,
                                                     SalesInvoice.balance_due > 0)
                          .order_by(SalesInvoice.due_date, SalesInvoice.number)).scalars():
        if remaining <= 0:
            break
        take = min(remaining, inv.balance_due)
        allocs.append(AllocationInput(inv.id, take))
        remaining -= take
    rc = receivables.post_receipt(db, user_id=user_id, customer_id=customer_id, method=method, amount=amount,
                                  allocations=allocs, reference=f"Caja {s.number}", idempotency_key=idempotency_key)
    db.flush()
    if not db.execute(select(CashMovement).where(CashMovement.receipt_id == rc.id)).scalar_one_or_none():
        db.add(CashMovement(id=uuid.uuid4(), session_id=s.id, kind=CashMovementKind.COLLECTION, method=method,
                            direction=1, amount=amount, receipt_id=rc.id, description=f"Cobro {rc.number}",
                            created_by_user_id=user_id))
        db.flush()
    return rc


# --- Tablero operativo ----------------------------------------------------------------


def dashboard(db: Session, day: date) -> dict:
    company = db.get(Company, 1)
    tz = ZoneInfo(company.timezone if company else "America/Asuncion")
    start = datetime.combine(day, time.min, tz)
    end = start + timedelta(days=1)
    in_day = (PosSale.created_at >= start) & (PosSale.created_at < end)
    rows = list(db.execute(select(PosSale).where(in_day)).scalars())
    done = [r for r in rows if r.status == PosSaleStatus.COMPLETED]
    total = sum((r.total for r in done), D0)
    by_method: dict[str, Decimal] = {}
    for r in done:
        for p in r.payments:
            by_method[p["method"]] = by_method.get(p["method"], D0) + Decimal(p["amount"])
        if r.change_given:
            by_method["CASH"] = by_method.get("CASH", D0) - r.change_given
    by_user: dict[str, dict] = {}
    for r in done:
        u = by_user.setdefault(str(r.created_by_user_id), {"count": 0, "total": D0})
        u["count"] += 1
        u["total"] += r.total
    top = db.execute(select(SalesOrderLine.product_id, Product.name, func.sum(SalesOrderLine.quantity),
                            func.sum(SalesOrderLine.line_total))
                     .join(PosSale, PosSale.order_id == SalesOrderLine.order_id)
                     .join(Product, Product.id == SalesOrderLine.product_id)
                     .where(in_day, PosSale.status == PosSaleStatus.COMPLETED)
                     .group_by(SalesOrderLine.product_id, Product.name)
                     .order_by(func.sum(SalesOrderLine.line_total).desc()).limit(10)).all()
    sessions = list(db.execute(select(CashSession).where(or_(
        CashSession.status == SessionStatus.OPEN, (CashSession.closed_at >= start) & (CashSession.closed_at < end)))
        .order_by(CashSession.opened_at)).scalars())
    return {
        "date": day, "sales_count": len(done), "sales_total": total,
        "average_ticket": (total / len(done)).quantize(Decimal("1")) if done else D0,
        "credit_total": sum((r.total for r in done if r.paid < r.total), D0) - sum((r.paid for r in done if r.paid < r.total), D0),
        "voided_count": len(rows) - len(done), "by_method": by_method, "by_user": by_user,
        "top_products": [{"product_id": pid, "name": n, "quantity": qy, "total": t} for pid, n, qy, t in top],
        "sessions": [{"id": x.id, "number": x.number, "register_id": x.register_id, "status": x.status,
                      "opened_by_user_id": x.opened_by_user_id, "opened_at": x.opened_at,
                      "expected_cash": x.expected_cash if x.status == SessionStatus.CLOSED else expected_cash(db, x),
                      "counted_cash": x.counted_cash, "difference": x.difference} for x in sessions],
    }
