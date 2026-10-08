"""Motor contable: asientos de doble partida, asientos automaticos desde las
operaciones, periodos e informes de gestion.

Atomicidad: los asientos automaticos se generan DENTRO de la misma
transaccion que la operacion que los origina (venta, cobro, recepcion...).
Si el asiento no se puede registrar (periodo cerrado, cuenta mal mapeada),
la operacion entera se revierte -- no puede quedar una venta sin su asiento.

Periodos: se crean OPEN al primer asiento del mes. Registrar toma un lock
compartido sobre el periodo y cerrarlo uno exclusivo, asi un cierre
concurrente no puede "colarse" entre la validacion y el insert.

Los informes son de GESTION: no reemplazan libros rubricados ni la
liquidacion tributaria, que valida un contador."""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.services.sales import NotFound, SalesError, _currency, next_number
from app.tenant_models.accounting import (
    Account,
    AccountMapping,
    AccountType,
    FiscalPeriod,
    JournalEntry,
    JournalLine,
    PeriodStatus,
)
from app.tenant_models.inventory import MovementType, StockMovement
from app.tenant_models.receivables import PaymentMethod

D0 = Decimal("0")


class AccountingError(SalesError):
    pass


class PeriodClosed(AccountingError):
    status_code = 409


@dataclass
class Line:
    account_id: uuid.UUID
    debit: Decimal = D0
    credit: Decimal = D0
    party_id: uuid.UUID | None = None
    description: str | None = None


@dataclass
class EntryDraft:
    entry_date: date
    description: str
    source_type: str
    source_id: uuid.UUID | None
    lines: list[Line] = field(default_factory=list)


def _today(db: Session) -> date:
    from app.services.receivables import local_today

    return local_today(db)


def _unit(db: Session) -> Decimal:
    return Decimal(1).scaleb(-_currency(db).decimals)


def account_for(db: Session, key: str) -> uuid.UUID:
    account_id = db.execute(select(AccountMapping.account_id).where(AccountMapping.key == key)).scalar_one_or_none()
    if account_id is None:
        raise AccountingError(f"Falta configurar la cuenta para '{key}' (Contabilidad > Mapeo de cuentas).")
    return account_id


def _period_for(db: Session, d: date, lock_exclusive: bool = False) -> FiscalPeriod:
    db.execute(
        insert(FiscalPeriod).values(id=uuid.uuid4(), year=d.year, month=d.month, status=PeriodStatus.OPEN)
        .on_conflict_do_nothing(index_elements=["year", "month"])
    )
    stmt = select(FiscalPeriod).where(FiscalPeriod.year == d.year, FiscalPeriod.month == d.month)
    stmt = stmt.with_for_update() if lock_exclusive else stmt.with_for_update(read=True)
    return db.execute(stmt.execution_options(populate_existing=True)).scalar_one()


def post_entry(db: Session, draft: EntryDraft, user_id: uuid.UUID | None = None,
               reverses: uuid.UUID | None = None) -> JournalEntry | None:
    """Registra un asiento. Lineas en cero se descartan; si no queda nada
    (operacion de valor cero), no se registra asiento y devuelve None."""
    unit = _unit(db)
    lines = []
    for ln in draft.lines:
        debit, credit = Decimal(ln.debit).quantize(unit, ROUND_HALF_UP), Decimal(ln.credit).quantize(unit, ROUND_HALF_UP)
        if debit < 0 or credit < 0:
            raise AccountingError("Debe y haber no pueden ser negativos.")
        if debit > 0 and credit > 0:
            raise AccountingError("Una linea no puede tener debe y haber a la vez.")
        if debit or credit:
            lines.append(Line(ln.account_id, debit, credit, ln.party_id, ln.description))
    if not lines:
        return None
    total_d, total_c = sum(ln.debit for ln in lines), sum(ln.credit for ln in lines)
    if total_d != total_c or len(lines) < 2:
        raise AccountingError(f"Asiento desbalanceado: debe {total_d}, haber {total_c}.")

    accounts = {a.id: a for a in db.execute(select(Account).where(Account.id.in_({ln.account_id for ln in lines}))).scalars()}
    for ln in lines:
        acc = accounts.get(ln.account_id)
        if acc is None or not acc.is_postable or not acc.is_active:
            raise AccountingError("Cuenta inexistente, inactiva o no imputable.")

    period = _period_for(db, draft.entry_date)
    if period.status != PeriodStatus.OPEN:
        raise PeriodClosed(f"El periodo {period.month:02d}/{period.year} esta cerrado.")

    entry = JournalEntry(
        id=uuid.uuid4(), number=next_number(db, "JOURNAL_ENTRY"), entry_date=draft.entry_date, period_id=period.id,
        description=draft.description[:300], source_type=draft.source_type, source_id=draft.source_id,
        reverses_entry_id=reverses, created_by_user_id=user_id,
    )
    db.add(entry)
    for i, ln in enumerate(lines, start=1):
        db.add(JournalLine(entry_id=entry.id, line_no=i, account_id=ln.account_id, debit=ln.debit, credit=ln.credit,
                           party_id=ln.party_id, description=ln.description))
    db.flush()
    return entry


def reverse_entry(db: Session, entry: JournalEntry, user_id: uuid.UUID | None, description: str | None = None,
                  entry_date: date | None = None) -> JournalEntry:
    if entry.reverses_entry_id is not None:
        raise AccountingError("No se revierte un asiento de reversion.")
    if db.execute(select(JournalEntry.id).where(JournalEntry.reverses_entry_id == entry.id)).first():
        raise PeriodClosed("El asiento ya fue revertido.")
    draft = EntryDraft(
        entry_date=entry_date or _today(db), description=description or f"Reversion de {entry.number}",
        source_type=entry.source_type, source_id=entry.source_id,
        lines=[Line(ln.account_id, ln.credit, ln.debit, ln.party_id, ln.description) for ln in entry.lines],
    )
    return post_entry(db, draft, user_id, reverses=entry.id)


def reverse_source(db: Session, source_types: tuple[str, ...], source_id: uuid.UUID, user_id: uuid.UUID | None,
                   description: str) -> None:
    reversed_ids = select(JournalEntry.reverses_entry_id).where(JournalEntry.reverses_entry_id.is_not(None))
    entries = db.execute(
        select(JournalEntry).where(
            JournalEntry.source_type.in_(source_types), JournalEntry.source_id == source_id,
            JournalEntry.reverses_entry_id.is_(None), JournalEntry.id.not_in(reversed_ids),
        ).order_by(JournalEntry.created_at)
    ).scalars().all()
    for entry in entries:
        reverse_entry(db, entry, user_id, description)


def _cash_account(db: Session, method: PaymentMethod) -> uuid.UUID:
    return account_for(db, "CASH" if method == PaymentMethod.CASH else "BANK")


# --- Asientos automaticos -----------------------------------------------------------


def post_stock_movement(db: Session, m: StockMovement, source: str | None, user_id: uuid.UUID | None) -> None:
    if m.movement_type in (MovementType.TRANSFER_IN, MovementType.TRANSFER_OUT):
        return  # mismo activo, distinto deposito: no hay asiento
    if m.movement_type == MovementType.REVERSAL:
        reverse_source(db, ("STOCK_MOVEMENT",), m.reverses_movement_id, user_id, f"Reversion de stock {m.reference or ''}".strip())
        return
    value = (m.quantity * m.unit_cost).quantize(_unit(db), ROUND_HALF_UP)
    inv = account_for(db, "INVENTORY")
    if m.movement_type == MovementType.RECEIPT:
        other = account_for(db, "GRNI" if source == "PURCHASE_ORDER" else "INVENTORY_ADJUSTMENT")
        lines = [Line(inv, debit=value), Line(other, credit=value)]
        desc = f"Entrada de stock {m.reference or ''}"
    elif m.movement_type == MovementType.ISSUE:
        other = account_for(db, "COGS" if source == "SALES_ORDER" else "INVENTORY_ADJUSTMENT")
        lines = [Line(other, debit=value), Line(inv, credit=value)]
        desc = f"{'Costo de venta' if source == 'SALES_ORDER' else 'Salida de stock'} {m.reference or ''}"
    elif m.movement_type == MovementType.ADJUSTMENT_IN:
        lines = [Line(inv, debit=value), Line(account_for(db, "INVENTORY_ADJUSTMENT"), credit=value)]
        desc = "Ajuste de inventario (+)"
    elif m.movement_type == MovementType.ADJUSTMENT_OUT:
        lines = [Line(account_for(db, "INVENTORY_ADJUSTMENT"), debit=value), Line(inv, credit=value)]
        desc = "Ajuste de inventario (-)"
    else:
        return
    post_entry(db, EntryDraft(_today(db), desc.strip(), "STOCK_MOVEMENT", m.id, lines), user_id)


def post_sales_invoice(db: Session, inv, user_id) -> None:
    net = inv.taxable_10 + inv.taxable_5 + inv.exempt
    post_entry(db, EntryDraft(inv.issue_date, f"Factura interna {inv.number}", "SALES_INVOICE", inv.id, [
        Line(account_for(db, "AR"), debit=inv.total, party_id=inv.customer_id),
        Line(account_for(db, "SALES_REVENUE"), credit=net),
        Line(account_for(db, "VAT_OUTPUT_10"), credit=inv.vat_10),
        Line(account_for(db, "VAT_OUTPUT_5"), credit=inv.vat_5),
    ]), user_id)


def post_customer_receipt(db: Session, rc, applied: Decimal, user_id) -> None:
    post_entry(db, EntryDraft(rc.receipt_date, f"Cobro {rc.number}", "CUSTOMER_RECEIPT", rc.id, [
        Line(_cash_account(db, rc.method), debit=rc.amount),
        Line(account_for(db, "AR"), credit=applied, party_id=rc.customer_id),
        Line(account_for(db, "CUSTOMER_ADVANCES"), credit=rc.amount - applied, party_id=rc.customer_id),
    ]), user_id)


def post_customer_advance_application(db: Session, rc, amount: Decimal, user_id) -> None:
    post_entry(db, EntryDraft(_today(db), f"Aplicacion de anticipo {rc.number}", "CUSTOMER_RECEIPT_APPLICATION", rc.id, [
        Line(account_for(db, "CUSTOMER_ADVANCES"), debit=amount, party_id=rc.customer_id),
        Line(account_for(db, "AR"), credit=amount, party_id=rc.customer_id),
    ]), user_id)


def post_supplier_invoice(db: Session, inv, user_id) -> None:
    net = inv.taxable_10 + inv.taxable_5 + inv.exempt
    debit_key = "GRNI" if inv.purchase_order_id else "EXPENSE_DEFAULT"
    post_entry(db, EntryDraft(inv.issue_date, f"Factura proveedor {inv.supplier_invoice_number}", "SUPPLIER_INVOICE", inv.id, [
        Line(account_for(db, debit_key), debit=net),
        Line(account_for(db, "VAT_INPUT"), debit=inv.vat_10 + inv.vat_5),
        Line(account_for(db, "AP"), credit=inv.total, party_id=inv.supplier_id),
    ]), user_id)


def post_supplier_payment(db: Session, pay, applied: Decimal, user_id) -> None:
    post_entry(db, EntryDraft(pay.payment_date, f"Pago {pay.number}", "SUPPLIER_PAYMENT", pay.id, [
        Line(account_for(db, "AP"), debit=applied, party_id=pay.supplier_id),
        Line(account_for(db, "SUPPLIER_ADVANCES"), debit=pay.amount - applied, party_id=pay.supplier_id),
        Line(_cash_account(db, pay.method), credit=pay.amount),
    ]), user_id)


def post_supplier_advance_application(db: Session, pay, amount: Decimal, user_id) -> None:
    post_entry(db, EntryDraft(_today(db), f"Aplicacion de anticipo {pay.number}", "SUPPLIER_PAYMENT_APPLICATION", pay.id, [
        Line(account_for(db, "AP"), debit=amount, party_id=pay.supplier_id),
        Line(account_for(db, "SUPPLIER_ADVANCES"), credit=amount, party_id=pay.supplier_id),
    ]), user_id)


# --- Periodos -------------------------------------------------------------------------


def set_period_status(db: Session, year: int, month: int, status: PeriodStatus, user_id, note: str | None) -> FiscalPeriod:
    if not 1 <= month <= 12:
        raise AccountingError("Mes invalido.")
    period = _period_for(db, date(year, month, 1), lock_exclusive=True)
    period.status = status
    period.note = note
    if status == PeriodStatus.CLOSED:
        period.closed_at = datetime.now(timezone.utc)
        period.closed_by_user_id = user_id
    return period


# --- Informes -------------------------------------------------------------------------


def _sums(db: Session, date_from: date | None, date_to: date | None):
    stmt = (
        select(JournalLine.account_id, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .group_by(JournalLine.account_id)
    )
    if date_from:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)
    return {aid: (Decimal(d), Decimal(c)) for aid, d, c in db.execute(stmt).all()}


def _accounts(db: Session) -> list[Account]:
    return list(db.execute(select(Account).where(Account.is_postable).order_by(Account.code)).scalars())


def trial_balance(db: Session, date_from: date, date_to: date) -> list[dict]:
    from datetime import timedelta

    opening = _sums(db, None, date_from - timedelta(days=1))
    period = _sums(db, date_from, date_to)
    rows = []
    for acc in _accounts(db):
        od, oc = opening.get(acc.id, (D0, D0))
        pd, pc = period.get(acc.id, (D0, D0))
        if not (od or oc or pd or pc):
            continue
        rows.append({"account_id": acc.id, "code": acc.code, "name": acc.name, "account_type": acc.account_type,
                     "opening_balance": od - oc, "debit": pd, "credit": pc, "closing_balance": od - oc + pd - pc})
    return rows


def ledger(db: Session, account_id: uuid.UUID, date_from: date, date_to: date, limit: int, offset: int) -> dict:
    from datetime import timedelta

    acc = db.get(Account, account_id)
    if acc is None:
        raise NotFound("Cuenta no encontrada.")
    od, oc = _sums(db, None, date_from - timedelta(days=1)).get(account_id, (D0, D0))
    base = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == account_id, JournalEntry.entry_date >= date_from, JournalEntry.entry_date <= date_to)
    )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    ordered = base.order_by(JournalEntry.entry_date, JournalEntry.number, JournalLine.line_no)
    # Saldo de arranque de la pagina = apertura + todo lo anterior a offset.
    before = 0
    if offset:
        prev = ordered.with_only_columns(JournalLine.debit.label("d"), JournalLine.credit.label("c")).limit(offset).subquery()
        before = db.execute(select(func.coalesce(func.sum(prev.c.d - prev.c.c), 0))).scalar_one()
    running = od - oc + Decimal(before)
    rows = []
    for ln, entry in db.execute(ordered.limit(limit).offset(offset)).all():
        running += ln.debit - ln.credit
        rows.append({"entry_id": entry.id, "number": entry.number, "entry_date": entry.entry_date,
                     "description": ln.description or entry.description, "source_type": entry.source_type,
                     "debit": ln.debit, "credit": ln.credit, "balance": running})
    return {"account_id": acc.id, "code": acc.code, "name": acc.name, "opening_balance": od - oc,
            "total": total, "items": rows}


def income_statement(db: Session, date_from: date, date_to: date) -> dict:
    sums = _sums(db, date_from, date_to)
    income, expense = [], []
    for acc in _accounts(db):
        d, c = sums.get(acc.id, (D0, D0))
        if acc.account_type == AccountType.INCOME and (d or c):
            income.append({"code": acc.code, "name": acc.name, "amount": c - d})
        elif acc.account_type == AccountType.EXPENSE and (d or c):
            expense.append({"code": acc.code, "name": acc.name, "amount": d - c})
    ti, te = sum((r["amount"] for r in income), D0), sum((r["amount"] for r in expense), D0)
    return {"date_from": date_from, "date_to": date_to, "income": income, "expense": expense,
            "total_income": ti, "total_expense": te, "net_result": ti - te}


def balance_sheet(db: Session, as_of: date) -> dict:
    sums = _sums(db, None, as_of)
    groups = {AccountType.ASSET: [], AccountType.LIABILITY: [], AccountType.EQUITY: []}
    result = D0
    for acc in _accounts(db):
        d, c = sums.get(acc.id, (D0, D0))
        if not (d or c):
            continue
        if acc.account_type == AccountType.ASSET:
            groups[acc.account_type].append({"code": acc.code, "name": acc.name, "amount": d - c})
        elif acc.account_type in (AccountType.LIABILITY, AccountType.EQUITY):
            groups[acc.account_type].append({"code": acc.code, "name": acc.name, "amount": c - d})
        else:
            result += c - d  # ingresos - gastos todavia no cerrados a resultados acumulados
    ta = sum((r["amount"] for r in groups[AccountType.ASSET]), D0)
    tl = sum((r["amount"] for r in groups[AccountType.LIABILITY]), D0)
    te = sum((r["amount"] for r in groups[AccountType.EQUITY]), D0)
    return {"as_of": as_of, "assets": groups[AccountType.ASSET], "liabilities": groups[AccountType.LIABILITY],
            "equity": groups[AccountType.EQUITY], "current_result": result, "total_assets": ta,
            "total_liabilities": tl, "total_equity": te + result, "balanced": ta == tl + te + result}

