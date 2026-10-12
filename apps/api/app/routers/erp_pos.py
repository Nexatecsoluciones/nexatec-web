"""Venta rapida (POS), caja y codigos de barras. Logica en app/services/pos.py.

Permisos: pos:sell (vender), cash:operate (abrir/cerrar SU caja, movimientos,
cobros de saldo), cash:manage (cajas, todos los turnos, anular ventas,
tablero), pos:discount (cambiar precio / descuento), products:write
(codigos de barras)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.models.control_plane import User
from app.security.erp_context import ErpContext, require
from app.services import inventory, pos
from app.services.receivables import local_today
from app.services.sales import SalesError
from app.tenant_models.core import Product
from app.tenant_models.pos import (
    CashMovement, CashMovementKind, CashRegister, CashSession, PosSale, PosSaleStatus, ProductBarcode, SessionStatus,
    Symbology,
)
from app.tenant_models.receivables import PaymentMethod, SalesInvoice
from app.tenant_models.sales import PaymentCondition, SalesOrderLine

router = APIRouter(prefix="/api/erp/{system_access_id}", tags=["erp", "pos"])

Money = Field(ge=0, max_digits=18, decimal_places=2)


def _run(ctx: ErpContext, action: str, fn, resource: str, meta=None):
    try:
        obj = fn()
        ctx.db.commit()
    except (SalesError, inventory.InventoryError) as exc:
        ctx.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except IntegrityError as exc:
        ctx.db.rollback()
        msg = str(exc.orig)
        if "uq_cash_sessions_open" in msg:
            raise HTTPException(status_code=409, detail="Ya hay un turno abierto para esa caja o ese usuario.")
        if "cash_registers_code_key" in msg:
            raise HTTPException(status_code=409, detail="Ese codigo de caja ya existe.")
        if "uq_product_barcodes_code" in msg:
            raise HTTPException(status_code=409, detail="Ese codigo de barras ya esta asignado.")
        raise HTTPException(status_code=409, detail="Conflicto o dato duplicado.")
    if obj is not None and not isinstance(obj, dict):
        ctx.db.refresh(obj)
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action=action,
              resource=f"{resource}:{getattr(obj, 'id', '')}", metadata=meta(obj) if meta else None)
    ctx.control_db.commit()
    return obj


def _404(what="Recurso"):
    return HTTPException(status_code=404, detail=f"{what} no encontrado.")


# --- Codigos de barras ----------------------------------------------------------------


class BarcodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=48)
    primary: bool = False


class BarcodeOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    code: str
    symbology: Symbology
    is_primary: bool
    is_internal: bool
    model_config = ConfigDict(from_attributes=True)


@router.get("/products/{product_id}/barcodes", response_model=list[BarcodeOut])
def list_barcodes(product_id: uuid.UUID, ctx: ErpContext = Depends(require("products:read"))):
    return list(ctx.db.execute(select(ProductBarcode).where(ProductBarcode.product_id == product_id)
                               .order_by(ProductBarcode.is_primary.desc(), ProductBarcode.created_at)).scalars())


@router.post("/products/{product_id}/barcodes", response_model=BarcodeOut, status_code=201)
def add_barcode(product_id: uuid.UUID, payload: BarcodeIn, ctx: ErpContext = Depends(require("products:write"))):
    return _run(ctx, "ERP_BARCODE_ADDED", lambda: pos.add_barcode(ctx.db, product_id, payload.code, payload.primary),
                "product_barcode", lambda b: {"code": b.code, "product_id": str(b.product_id)})


@router.post("/products/{product_id}/barcodes/generate", response_model=BarcodeOut, status_code=201)
def generate_barcode(product_id: uuid.UUID, ctx: ErpContext = Depends(require("products:write"))):
    return _run(ctx, "ERP_BARCODE_GENERATED", lambda: pos.generate_internal(ctx.db, product_id), "product_barcode",
                lambda b: {"code": b.code, "product_id": str(b.product_id)})


@router.delete("/products/{product_id}/barcodes/{barcode_id}", status_code=204)
def delete_barcode(product_id: uuid.UUID, barcode_id: uuid.UUID, ctx: ErpContext = Depends(require("products:write"))):
    def fn():
        b = ctx.db.get(ProductBarcode, barcode_id)
        if b is None or b.product_id != product_id:
            raise SalesError("Codigo inexistente.")
        ctx.db.delete(b)
        return {"code": b.code}
    _run(ctx, "ERP_BARCODE_REMOVED", fn, "product_barcode", lambda r: r)


class LabelOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    sale_price: Decimal
    code: str | None
    symbology: Symbology | None


@router.get("/products-labels", response_model=list[LabelOut])
def labels(ctx: ErpContext = Depends(require("products:read")), ids: list[uuid.UUID] = Query(..., max_length=500)):
    """Datos para imprimir etiquetas (codigo principal de cada producto)."""
    prods = {p.id: p for p in ctx.db.execute(select(Product).where(Product.id.in_(ids))).scalars()}
    codes = {b.product_id: b for b in ctx.db.execute(select(ProductBarcode).where(
        ProductBarcode.product_id.in_(ids), ProductBarcode.is_primary)).scalars()}
    out = []
    for pid in ids:
        p = prods.get(pid)
        if p is None:
            continue
        b = codes.get(pid)
        out.append(LabelOut(product_id=p.id, sku=p.sku, name=p.name, sale_price=p.sale_price,
                            code=b.code if b else None, symbology=b.symbology if b else None))
    return out


# --- Cajas ----------------------------------------------------------------------------


class RegisterIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=2, max_length=120)
    warehouse_id: uuid.UUID
    default_customer_id: uuid.UUID | None = None
    is_active: bool = True


class RegisterOut(RegisterIn):
    id: uuid.UUID
    model_config = ConfigDict(from_attributes=True)


@router.get("/pos/registers", response_model=list[RegisterOut])
def list_registers(ctx: ErpContext = Depends(require("cash:operate"))):
    return list(ctx.db.execute(select(CashRegister).order_by(CashRegister.code)).scalars())


@router.post("/pos/registers", response_model=RegisterOut, status_code=201)
def create_register(payload: RegisterIn, ctx: ErpContext = Depends(require("cash:manage"))):
    def fn():
        r = CashRegister(id=uuid.uuid4(), **(payload.model_dump() | {"code": payload.code.strip().upper()}))
        ctx.db.add(r)
        ctx.db.flush()
        return r
    return _run(ctx, "ERP_CASH_REGISTER_CREATED", fn, "cash_register")


@router.put("/pos/registers/{register_id}", response_model=RegisterOut)
def update_register(register_id: uuid.UUID, payload: RegisterIn, ctx: ErpContext = Depends(require("cash:manage"))):
    def fn():
        r = ctx.db.get(CashRegister, register_id)
        if r is None:
            raise SalesError("Caja inexistente.")
        for k, v in payload.model_dump().items():
            setattr(r, k, v.strip().upper() if k == "code" else v)
        return r
    return _run(ctx, "ERP_CASH_REGISTER_UPDATED", fn, "cash_register")


# --- Turnos ---------------------------------------------------------------------------


class SessionOut(BaseModel):
    id: uuid.UUID
    number: str
    register_id: uuid.UUID
    status: SessionStatus
    opened_by_user_id: uuid.UUID
    opened_by: str | None = None
    opened_at: datetime
    opening_float: Decimal
    closed_at: datetime | None
    expected_cash: Decimal | None
    counted_cash: Decimal | None
    difference: Decimal | None
    count_detail: dict | None
    closing_notes: str | None
    model_config = ConfigDict(from_attributes=True)


class MovementOut(BaseModel):
    id: uuid.UUID
    kind: CashMovementKind
    method: PaymentMethod
    direction: int
    amount: Decimal
    description: str | None
    pos_sale_id: uuid.UUID | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SessionDetail(BaseModel):
    session: SessionOut
    summary: dict
    movements: list[MovementOut]


def _emails(ctx: ErpContext, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(ctx.control_db.execute(select(User.id, User.email).where(User.id.in_(ids))).all())


def _session_out(ctx: ErpContext, s: CashSession, emails=None) -> SessionOut:
    out = SessionOut.model_validate(s)
    out.opened_by = (emails or _emails(ctx, [s.opened_by_user_id])).get(s.opened_by_user_id)
    return out


def _visible(ctx: ErpContext, s: CashSession) -> None:
    if s.opened_by_user_id != ctx.user.id and not ctx.can("cash:manage"):
        raise _404("Turno")


def _detail(ctx: ErpContext, s: CashSession) -> SessionDetail:
    moves = list(ctx.db.execute(select(CashMovement).where(CashMovement.session_id == s.id)
                                .order_by(CashMovement.created_at.desc())).scalars())
    return SessionDetail(session=_session_out(ctx, s), summary=pos.summary(ctx.db, s),
                         movements=[MovementOut.model_validate(m) for m in moves])


@router.get("/pos/session/current", response_model=SessionDetail | None)
def current_session(ctx: ErpContext = Depends(require("cash:operate"))):
    s = pos.my_open_session(ctx.db, ctx.user.id)
    return _detail(ctx, s) if s else None


class OpenIn(BaseModel):
    register_id: uuid.UUID
    opening_float: Decimal = Money


@router.post("/pos/sessions", response_model=SessionDetail, status_code=201)
def open_session(payload: OpenIn, ctx: ErpContext = Depends(require("cash:operate"))):
    s = _run(ctx, "ERP_CASH_SESSION_OPENED", lambda: pos.open_session(ctx.db, payload.register_id, ctx.user.id,
                                                                      payload.opening_float), "cash_session",
             lambda s: {"number": s.number, "opening_float": str(s.opening_float)})
    return _detail(ctx, s)


@router.get("/pos/sessions", response_model=list[SessionOut])
def list_sessions(ctx: ErpContext = Depends(require("cash:operate")), date_from: date | None = None,
                  date_to: date | None = None, open_only: bool = False):
    stmt = select(CashSession)
    if not ctx.can("cash:manage"):
        stmt = stmt.where(CashSession.opened_by_user_id == ctx.user.id)
    if open_only:
        stmt = stmt.where(CashSession.status == SessionStatus.OPEN)
    if date_from:
        stmt = stmt.where(func.date(CashSession.opened_at) >= date_from)
    if date_to:
        stmt = stmt.where(func.date(CashSession.opened_at) <= date_to)
    rows = list(ctx.db.execute(stmt.order_by(CashSession.opened_at.desc()).limit(200)).scalars())
    emails = _emails(ctx, [s.opened_by_user_id for s in rows])
    return [_session_out(ctx, s, emails) for s in rows]


@router.get("/pos/sessions/{session_id}", response_model=SessionDetail)
def get_session(session_id: uuid.UUID, ctx: ErpContext = Depends(require("cash:operate"))):
    s = ctx.db.get(CashSession, session_id)
    if s is None:
        raise _404("Turno")
    _visible(ctx, s)
    return _detail(ctx, s)


class MovementIn(BaseModel):
    kind: Literal["CASH_IN", "EXPENSE", "TO_BANK", "FROM_BANK"]
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    description: str = Field(min_length=3, max_length=250)


@router.post("/pos/sessions/{session_id}/movements", response_model=SessionDetail, status_code=201)
def add_movement(session_id: uuid.UUID, payload: MovementIn, ctx: ErpContext = Depends(require("cash:operate"))):
    s = ctx.db.get(CashSession, session_id)
    if s is None or s.opened_by_user_id != ctx.user.id:
        raise _404("Turno")
    _run(ctx, f"ERP_CASH_{payload.kind}", lambda: pos.manual_movement(
        ctx.db, session_id=session_id, user_id=ctx.user.id, kind=CashMovementKind(payload.kind), amount=payload.amount,
        description=payload.description), "cash_movement", lambda m: {"amount": str(m.amount), "kind": m.kind.value})
    return _detail(ctx, ctx.db.get(CashSession, session_id))


class CloseIn(BaseModel):
    counted_cash: Decimal = Money
    count_detail: dict[str, int] | None = None
    notes: str | None = Field(default=None, max_length=1000)


@router.post("/pos/sessions/{session_id}/close", response_model=SessionDetail)
def close_session(session_id: uuid.UUID, payload: CloseIn, ctx: ErpContext = Depends(require("cash:operate"))):
    s = ctx.db.get(CashSession, session_id)
    if s is None:
        raise _404("Turno")
    _visible(ctx, s)
    s = _run(ctx, "ERP_CASH_SESSION_CLOSED", lambda: pos.close_session(
        ctx.db, session_id=session_id, user_id=ctx.user.id, counted_cash=payload.counted_cash,
        count_detail=payload.count_detail, notes=payload.notes, can_close_others=ctx.can("cash:manage")),
        "cash_session", lambda s: {"number": s.number, "expected": str(s.expected_cash), "counted": str(s.counted_cash),
                                   "difference": str(s.difference)})
    return _detail(ctx, s)


# --- Venta rapida ---------------------------------------------------------------------


class SearchOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    sale_price: Decimal
    tax_code: str
    tracks_stock: bool
    available: Decimal | None


def _register_wh(ctx: ErpContext):
    s = pos.my_open_session(ctx.db, ctx.user.id)
    return ctx.db.get(CashRegister, s.register_id).warehouse_id if s else None


@router.get("/pos/lookup", response_model=SearchOut | None)
def lookup(code: str = Query(min_length=1, max_length=48), ctx: ErpContext = Depends(require("pos:sell"))):
    """Lectura exacta (escaner o SKU). null si no existe."""
    return pos.lookup(ctx.db, code, _register_wh(ctx))


@router.get("/pos/search", response_model=list[SearchOut])
def search(q: str = Query(min_length=1, max_length=60), ctx: ErpContext = Depends(require("pos:sell"))):
    like = f"%{q.strip()}%"
    wh = _register_wh(ctx)
    prods = ctx.db.execute(select(Product).outerjoin(ProductBarcode, ProductBarcode.product_id == Product.id).where(
        Product.is_active.is_(True),
        or_(Product.name.ilike(like), Product.sku.ilike(like), ProductBarcode.code.ilike(like)),
    ).distinct().order_by(Product.name).limit(20)).scalars()
    return [pos.product_card(ctx.db, p, wh) for p in prods]


class SaleLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)


class PaymentIn(BaseModel):
    method: PaymentMethod
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    reference: str | None = Field(default=None, max_length=120)


class SaleIn(BaseModel):
    customer_id: uuid.UUID | None = None
    condition: PaymentCondition = PaymentCondition.CASH
    lines: list[SaleLineIn] = Field(min_length=1, max_length=200)
    payments: list[PaymentIn] = Field(default_factory=list, max_length=10)


class SaleOut(BaseModel):
    id: uuid.UUID
    status: PosSaleStatus
    session_id: uuid.UUID
    order_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str | None = None
    customer_id: uuid.UUID
    total: Decimal
    paid: Decimal
    tendered_cash: Decimal
    change_given: Decimal
    payments: list
    created_at: datetime
    void_reason: str | None
    replayed: bool = False
    model_config = ConfigDict(from_attributes=True)


def _sale_out(ctx: ErpContext, sale: PosSale, replayed=False) -> SaleOut:
    out = SaleOut.model_validate(sale)
    out.invoice_number = ctx.db.execute(select(SalesInvoice.number).where(SalesInvoice.id == sale.invoice_id)).scalar_one()
    out.replayed = replayed
    return out


@router.post("/pos/sales", response_model=SaleOut, status_code=201)
def create_sale(payload: SaleIn, ctx: ErpContext = Depends(require("pos:sell")),
                key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=100)):
    if not key:
        raise HTTPException(status_code=422, detail="Falta el encabezado Idempotency-Key (evita ventas duplicadas).")
    s = pos.my_open_session(ctx.db, ctx.user.id)
    if s is None:
        raise HTTPException(status_code=409, detail="Abri tu turno de caja antes de vender.")
    try:
        res = pos.sell(ctx.db, user_id=ctx.user.id, session_id=s.id, customer_id=payload.customer_id,
                       lines=[pos.PosLine(**ln.model_dump()) for ln in payload.lines], condition=payload.condition,
                       payments=[pos.PosPayment(**p.model_dump()) for p in payload.payments],
                       idempotency_key=key, can_discount=ctx.can("pos:discount"))
        ctx.db.commit()
    except (SalesError, inventory.InventoryError) as exc:
        ctx.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except IntegrityError:
        # Doble clic simultaneo con la misma clave: la otra ya la registro.
        ctx.db.rollback()
        prev = ctx.db.execute(select(PosSale).where(PosSale.idempotency_key == key)).scalar_one_or_none()
        if prev is None:
            raise HTTPException(status_code=409, detail="Conflicto al registrar la venta. Reintenta.")
        return _sale_out(ctx, prev, replayed=True)
    if not res.replayed:
        log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action="ERP_POS_SALE",
                  resource=f"pos_sale:{res.sale.id}", metadata={"total": str(res.sale.total), "session": s.number})
        ctx.control_db.commit()
    return _sale_out(ctx, res.sale, res.replayed)


@router.get("/pos/sales", response_model=list[SaleOut])
def list_sales(ctx: ErpContext = Depends(require("cash:operate")), session_id: uuid.UUID | None = None):
    stmt = select(PosSale)
    if session_id:
        s = ctx.db.get(CashSession, session_id)
        if s is None:
            raise _404("Turno")
        _visible(ctx, s)
        stmt = stmt.where(PosSale.session_id == session_id)
    elif not ctx.can("cash:manage"):
        stmt = stmt.where(PosSale.created_by_user_id == ctx.user.id)
    return [_sale_out(ctx, x) for x in ctx.db.execute(stmt.order_by(PosSale.created_at.desc()).limit(200)).scalars()]


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=250)


@router.post("/pos/sales/{sale_id}/void", response_model=SaleOut)
def void_sale(sale_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require("cash:manage"))):
    sale = _run(ctx, "ERP_POS_SALE_VOIDED", lambda: pos.void_sale(ctx.db, sale_id=sale_id, user_id=ctx.user.id,
                                                                  reason=payload.reason), "pos_sale",
                lambda x: {"total": str(x.total), "reason": payload.reason})
    return _sale_out(ctx, sale)


class CollectIn(BaseModel):
    customer_id: uuid.UUID
    method: PaymentMethod = PaymentMethod.CASH
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


@router.post("/pos/collections", status_code=201)
def collect(payload: CollectIn, ctx: ErpContext = Depends(require("cash:operate")),
            key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=100)):
    if not key:
        raise HTTPException(status_code=422, detail="Falta el encabezado Idempotency-Key.")
    s = pos.my_open_session(ctx.db, ctx.user.id)
    if s is None:
        raise HTTPException(status_code=409, detail="Abri tu turno de caja para cobrar.")
    rc = _run(ctx, "ERP_POS_COLLECTION", lambda: pos.collect_balance(
        ctx.db, session_id=s.id, user_id=ctx.user.id, customer_id=payload.customer_id, method=payload.method,
        amount=payload.amount, idempotency_key=key), "customer_receipt",
        lambda r: {"number": r.number, "amount": str(r.amount)})
    return {"receipt_id": rc.id, "number": rc.number, "applied": rc.amount - rc.unapplied_amount,
            "advance": rc.unapplied_amount}


# --- Tablero -------------------------------------------------------------------------


@router.get("/pos/dashboard")
def dashboard(ctx: ErpContext = Depends(require("cash:manage")), day: date | None = None):
    data = pos.dashboard(ctx.db, day or local_today(ctx.db))
    emails = _emails(ctx, [uuid.UUID(u) for u in data["by_user"] if u != "None"] + [s["opened_by_user_id"] for s in data["sessions"]])
    data["by_user"] = [{"user": emails.get(uuid.UUID(u)) if u != "None" else None, **v} for u, v in data["by_user"].items()]
    for s in data["sessions"]:
        s["opened_by"] = emails.get(s["opened_by_user_id"])
    regs = {r.id: r.code for r in ctx.db.execute(select(CashRegister)).scalars()}
    for s in data["sessions"]:
        s["register"] = regs.get(s["register_id"])
    return data
