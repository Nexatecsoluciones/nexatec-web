"""Contabilidad: plan de cuentas, mapeo, asientos (automaticos + manuales),
periodos e informes de gestion. Logica en app/services/accounting.py."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, get_erp_context, require
from app.services import accounting
from app.services.receivables import local_today
from app.services.sales import SalesError
from app.tenant_models.accounting import (
    Account,
    AccountMapping,
    AccountType,
    FiscalPeriod,
    JournalEntry,
    PeriodStatus,
)

router = APIRouter(prefix="/api/erp/{system_access_id}/accounting", tags=["erp", "accounting"])

MAX_PAGE = 200
MANAGEMENT_NOTICE = "Informe de gestion. No reemplaza libros rubricados ni la liquidacion tributaria."


def _commit(ctx: ErpContext, action: str, resource: str, metadata: dict | None = None) -> None:
    try:
        ctx.db.commit()
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Conflicto al guardar.")
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action=action,
              resource=resource, metadata=metadata)
    ctx.control_db.commit()


def _fail(ctx: ErpContext, exc: SalesError):
    ctx.db.rollback()
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


# --- Cuentas y mapeo -----------------------------------------------------------------


class AccountIn(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[0-9.]+$")
    name: str = Field(min_length=2, max_length=150)
    account_type: AccountType
    parent_id: uuid.UUID | None = None
    is_postable: bool = True


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    is_active: bool | None = None


class AccountOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    account_type: AccountType
    parent_id: uuid.UUID | None
    is_postable: bool
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class MappingIn(BaseModel):
    account_id: uuid.UUID


class MappingOut(BaseModel):
    key: str
    account_id: uuid.UUID
    model_config = ConfigDict(from_attributes=True)


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(ctx: ErpContext = Depends(require("accounting:read"))):
    return ctx.db.execute(select(Account).order_by(Account.code)).scalars().all()


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountIn, ctx: ErpContext = Depends(require("accounting:write"))):
    if payload.parent_id is not None:
        parent = ctx.db.get(Account, payload.parent_id)
        if parent is None or parent.account_type != payload.account_type:
            raise HTTPException(status_code=422, detail="La cuenta padre no existe o es de otro tipo.")
    acc = Account(**payload.model_dump(), is_active=True)
    ctx.db.add(acc)
    _commit(ctx, "ERP_ACCOUNT_CREATED", f"account:{acc.id}", {"code": acc.code})
    return acc


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: uuid.UUID, payload: AccountUpdate, ctx: ErpContext = Depends(require("accounting:write"))):
    acc = ctx.db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("is_active") is False and ctx.db.execute(
        select(AccountMapping.key).where(AccountMapping.account_id == acc.id)
    ).first():
        raise HTTPException(status_code=409, detail="La cuenta esta mapeada a un concepto automatico; cambiar el mapeo primero.")
    for k, v in updates.items():
        setattr(acc, k, v)
    _commit(ctx, "ERP_ACCOUNT_UPDATED", f"account:{acc.id}", {"fields": list(updates)})
    return acc


@router.get("/mappings", response_model=list[MappingOut])
def list_mappings(ctx: ErpContext = Depends(require("accounting:read"))):
    return ctx.db.execute(select(AccountMapping).order_by(AccountMapping.key)).scalars().all()


@router.put("/mappings/{key}", response_model=MappingOut)
def set_mapping(key: str, payload: MappingIn, ctx: ErpContext = Depends(require("accounting:write"))):
    mapping = ctx.db.get(AccountMapping, key)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Concepto inexistente.")
    acc = ctx.db.get(Account, payload.account_id)
    if acc is None or not acc.is_postable or not acc.is_active:
        raise HTTPException(status_code=422, detail="La cuenta no existe, esta inactiva o no es imputable.")
    mapping.account_id = acc.id
    _commit(ctx, "ERP_ACCOUNT_MAPPING_CHANGED", f"account_mapping:{key}", {"account_code": acc.code})
    return mapping


# --- Asientos -------------------------------------------------------------------------


class LineIn(BaseModel):
    account_id: uuid.UUID
    debit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    credit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    party_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=300)


class EntryIn(BaseModel):
    entry_date: date
    description: str = Field(min_length=3, max_length=300)
    lines: list[LineIn] = Field(min_length=2, max_length=200)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class LineOut(BaseModel):
    line_no: int
    account_id: uuid.UUID
    debit: Decimal
    credit: Decimal
    party_id: uuid.UUID | None
    description: str | None
    model_config = ConfigDict(from_attributes=True)


class EntryOut(BaseModel):
    id: uuid.UUID
    number: str
    entry_date: date
    description: str
    source_type: str
    source_id: uuid.UUID | None
    reverses_entry_id: uuid.UUID | None
    created_at: datetime
    lines: list[LineOut]
    model_config = ConfigDict(from_attributes=True)


class EntryPage(BaseModel):
    total: int
    items: list[EntryOut]


@router.post("/entries", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def create_manual_entry(payload: EntryIn, ctx: ErpContext = Depends(require("accounting:write"))):
    draft = accounting.EntryDraft(payload.entry_date, payload.description, "MANUAL", None, [
        accounting.Line(ln.account_id, ln.debit, ln.credit, ln.party_id, ln.description) for ln in payload.lines
    ])
    try:
        entry = accounting.post_entry(ctx.db, draft, ctx.user.id)
        if entry is None:
            raise accounting.AccountingError("El asiento no tiene importes.")
    except SalesError as exc:
        _fail(ctx, exc)
    _commit(ctx, "ERP_JOURNAL_ENTRY_CREATED", f"journal_entry:{entry.id}", {"number": entry.number})
    ctx.db.refresh(entry)
    return entry


@router.post("/entries/{entry_id}/reverse", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def reverse(entry_id: uuid.UUID, payload: ReverseIn, ctx: ErpContext = Depends(require("accounting:write"))):
    entry = ctx.db.get(JournalEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Asiento no encontrado.")
    if entry.source_type != "MANUAL":
        # Los automaticos se revierten anulando el documento que los origino
        # (factura, cobro...), para que documento y contabilidad no se separen.
        raise HTTPException(status_code=409, detail="Este asiento es automatico: anular el documento de origen.")
    try:
        rev = accounting.reverse_entry(ctx.db, entry, ctx.user.id, f"Reversion de {entry.number}: {payload.reason}")
    except SalesError as exc:
        _fail(ctx, exc)
    _commit(ctx, "ERP_JOURNAL_ENTRY_REVERSED", f"journal_entry:{rev.id}", {"reverses": entry.number})
    ctx.db.refresh(rev)
    return rev


@router.get("/entries/{entry_id}", response_model=EntryOut)
def get_entry(entry_id: uuid.UUID, ctx: ErpContext = Depends(require("accounting:read"))):
    entry = ctx.db.get(JournalEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Asiento no encontrado.")
    return entry


@router.get("/entries", response_model=EntryPage)
def list_entries(ctx: ErpContext = Depends(require("accounting:read")), date_from: date | None = None, date_to: date | None = None,
                 source_type: str | None = Query(default=None, max_length=40), source_id: uuid.UUID | None = None,
                 limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0)):
    """Libro diario."""
    stmt = select(JournalEntry)
    if date_from:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)
    if source_type:
        stmt = stmt.where(JournalEntry.source_type == source_type)
    if source_id:
        stmt = stmt.where(JournalEntry.source_id == source_id)
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = ctx.db.execute(stmt.order_by(JournalEntry.entry_date, JournalEntry.number).limit(limit).offset(offset)).scalars().all()
    return EntryPage(total=total, items=items)


# --- Periodos -------------------------------------------------------------------------


class PeriodOut(BaseModel):
    year: int
    month: int
    status: PeriodStatus
    closed_at: datetime | None
    note: str | None
    model_config = ConfigDict(from_attributes=True)


class PeriodNoteIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.get("/periods", response_model=list[PeriodOut])
def list_periods(ctx: ErpContext = Depends(require("accounting:read"))):
    return ctx.db.execute(select(FiscalPeriod).order_by(FiscalPeriod.year.desc(), FiscalPeriod.month.desc())).scalars().all()


@router.post("/periods/{year}/{month}/close", response_model=PeriodOut)
def close_period(year: int, month: int, payload: PeriodNoteIn, ctx: ErpContext = Depends(require("accounting:periods"))):
    try:
        p = accounting.set_period_status(ctx.db, year, month, PeriodStatus.CLOSED, ctx.user.id, payload.note)
    except SalesError as exc:
        _fail(ctx, exc)
    _commit(ctx, "ERP_PERIOD_CLOSED", f"fiscal_period:{year}-{month:02d}", {"note": payload.note})
    return p


@router.post("/periods/{year}/{month}/reopen", response_model=PeriodOut)
def reopen_period(year: int, month: int, payload: PeriodNoteIn, ctx: ErpContext = Depends(require("accounting:periods"))):
    try:
        p = accounting.set_period_status(ctx.db, year, month, PeriodStatus.OPEN, ctx.user.id, payload.note)
    except SalesError as exc:
        _fail(ctx, exc)
    _commit(ctx, "ERP_PERIOD_REOPENED", f"fiscal_period:{year}-{month:02d}", {"note": payload.note})
    return p


# --- Informes -------------------------------------------------------------------------


def _range(ctx: ErpContext, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = local_today(ctx.db)
    df, dt = date_from or today.replace(month=1, day=1), date_to or today
    if df > dt:
        raise HTTPException(status_code=422, detail="Rango de fechas invalido.")
    return df, dt


@router.get("/reports/trial-balance")
def trial_balance(ctx: ErpContext = Depends(require("accounting:read")), date_from: date | None = None, date_to: date | None = None):
    df, dt = _range(ctx, date_from, date_to)
    rows = accounting.trial_balance(ctx.db, df, dt)
    return {"notice": MANAGEMENT_NOTICE, "date_from": df, "date_to": dt, "rows": rows,
            "total_debit": sum((r["debit"] for r in rows), Decimal(0)),
            "total_credit": sum((r["credit"] for r in rows), Decimal(0))}


@router.get("/reports/ledger/{account_id}")
def ledger(account_id: uuid.UUID, ctx: ErpContext = Depends(require("accounting:read")), date_from: date | None = None,
           date_to: date | None = None, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    df, dt = _range(ctx, date_from, date_to)
    try:
        return {"notice": MANAGEMENT_NOTICE, **accounting.ledger(ctx.db, account_id, df, dt, limit, offset)}
    except SalesError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/reports/income-statement")
def income_statement(ctx: ErpContext = Depends(require("accounting:read")), date_from: date | None = None, date_to: date | None = None):
    df, dt = _range(ctx, date_from, date_to)
    return {"notice": MANAGEMENT_NOTICE, **accounting.income_statement(ctx.db, df, dt)}


@router.get("/reports/balance-sheet")
def balance_sheet(ctx: ErpContext = Depends(require("accounting:read")), as_of: date | None = None):
    return {"notice": MANAGEMENT_NOTICE, **accounting.balance_sheet(ctx.db, as_of or local_today(ctx.db))}
