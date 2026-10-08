import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.net import safe_ip
from app.models.media import MediaAsset
from app.models.payments import PaymentOrder, Plan, Subscription
from app.models.payments_enums import (
    BillingPeriod,
    Currency,
    PaymentMethod,
    PaymentOrderStatus,
    SubscriptionStatus,
)
from app.security.rbac import require_admin_panel
from app.security.session_auth import CurrentUser, get_current_user
from app.security.tenancy_rbac import assert_tenant_membership
from app.services.payments import bancard

router = APIRouter(tags=["payments"])

SUBSCRIPTION_PERIOD_DAYS = {"MONTHLY": 30, "ANNUAL": 365}


class PlanOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    price_amount: Decimal
    price_currency: Currency
    billing_period: str

    model_config = ConfigDict(from_attributes=True)


class PaymentOrderOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_id: uuid.UUID
    method: PaymentMethod
    status: PaymentOrderStatus
    amount: Decimal
    currency: Currency
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CheckoutRequest(BaseModel):
    tenant_id: uuid.UUID
    plan_id: uuid.UUID
    method: PaymentMethod


class CheckoutResponse(BaseModel):
    order: PaymentOrderOut
    bancard_process_id: str | None = None


class ReviewTransferRequest(BaseModel):
    note: str | None = None


class PlanCreate(BaseModel):
    slug: str
    name: str
    price_amount: Decimal
    price_currency: Currency = Currency.PYG
    billing_period: str


@router.post("/api/admin/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(payload: PlanCreate, db: Session = Depends(get_db), admin=Depends(require_admin_panel())):
    if db.execute(select(Plan).where(Plan.slug == payload.slug)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El slug ya existe.")
    plan = Plan(
        slug=payload.slug, name=payload.name, price_amount=payload.price_amount,
        price_currency=payload.price_currency, billing_period=BillingPeriod(payload.billing_period),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.get("/api/plans", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db)):
    return db.execute(select(Plan).where(Plan.is_active.is_(True))).scalars().all()


def _get_or_create_active_subscription(db: Session, tenant_id: uuid.UUID, plan_id: uuid.UUID) -> Subscription:
    sub = db.execute(
        select(Subscription).where(Subscription.tenant_id == tenant_id, Subscription.plan_id == plan_id)
    ).scalar_one_or_none()
    if sub is None:
        sub = Subscription(tenant_id=tenant_id, plan_id=plan_id, status=SubscriptionStatus.TRIAL)
        db.add(sub)
        db.flush()
    return sub


@router.post("/api/portal/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
async def create_checkout(
    payload: CheckoutRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    assert_tenant_membership(db, current_user, payload.tenant_id)

    settings = get_settings()
    enabled = (settings.self_checkout_transfer_enabled if payload.method == PaymentMethod.BANK_TRANSFER
               else settings.self_checkout_card_enabled)
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La contratacion en linea no esta habilitada. Escribinos por WhatsApp y te ayudamos a contratar.",
        )

    plan = db.get(Plan, payload.plan_id)
    if plan is None or not plan.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan no encontrado.")

    # El monto SIEMPRE sale del Plan en el servidor. El cliente nunca
    # envia un monto -- solo un plan_id.
    amount = plan.price_amount
    currency = plan.price_currency
    client_ip = safe_ip(request.client.host if request.client else None)

    subscription = _get_or_create_active_subscription(db, payload.tenant_id, payload.plan_id)

    if payload.method == PaymentMethod.BANK_TRANSFER:
        order = PaymentOrder(
            tenant_id=payload.tenant_id, subscription_id=subscription.id, plan_id=plan.id,
            method=PaymentMethod.BANK_TRANSFER, status=PaymentOrderStatus.PENDING_TRANSFER,
            amount=amount, currency=currency,
        )
        db.add(order)
        db.flush()
        log_audit(db, actor_user_id=current_user.id, tenant_id=payload.tenant_id, action="PAYMENT_ORDER_CREATED",
                   resource=f"payment_order:{order.id}", ip_address=client_ip, metadata={"method": "BANK_TRANSFER"})
        db.commit()
        db.refresh(order)
        return CheckoutResponse(order=order)

    # BANCARD_CARD
    shop_process_id = int(datetime.now(timezone.utc).timestamp() * 1000) % 2_000_000_000
    order = PaymentOrder(
        tenant_id=payload.tenant_id, subscription_id=subscription.id, plan_id=plan.id,
        method=PaymentMethod.BANCARD_CARD, status=PaymentOrderStatus.AWAITING_CARD_CONFIRMATION,
        amount=amount, currency=currency, shop_process_id=shop_process_id,
    )
    db.add(order)
    db.flush()

    try:
        result = await bancard.create_single_buy(
            shop_process_id=shop_process_id, amount=amount, currency=currency,
            description=f"NEXATEC - {plan.name}",
            return_url="https://staging.nexatecpy.com/portal",
            cancel_url="https://staging.nexatecpy.com/portal",
        )
    except bancard.BancardNotConfiguredError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El pago con tarjeta no esta disponible todavia (Bancard sin configurar).",
        )
    except bancard.BancardApiError as exc:
        order.status = PaymentOrderStatus.REJECTED
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Bancard rechazo la solicitud: {exc.key}")

    order.bancard_process_id = result.process_id
    log_audit(db, actor_user_id=current_user.id, tenant_id=payload.tenant_id, action="PAYMENT_ORDER_CREATED",
               resource=f"payment_order:{order.id}", ip_address=client_ip, metadata={"method": "BANCARD_CARD"})
    db.commit()
    db.refresh(order)
    return CheckoutResponse(order=order, bancard_process_id=result.process_id)


@router.post("/api/portal/payment-orders/{order_id}/proof", response_model=PaymentOrderOut)
def upload_transfer_proof(
    order_id: uuid.UUID,
    media_asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    order = db.get(PaymentOrder, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    assert_tenant_membership(db, current_user, order.tenant_id)

    if order.method != PaymentMethod.BANK_TRANSFER or order.status != PaymentOrderStatus.PENDING_TRANSFER:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La orden no admite comprobante.")

    media = db.get(MediaAsset, media_asset_id)
    if media is None or media.tenant_id != order.tenant_id:
        # Nunca aceptar un archivo de OTRO tenant como comprobante --
        # mismo principio anti-IDOR de siempre.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comprobante no encontrado.")

    order.transfer_proof_media_id = media.id
    order.status = PaymentOrderStatus.UNDER_REVIEW
    log_audit(db, actor_user_id=current_user.id, tenant_id=order.tenant_id, action="PAYMENT_PROOF_UPLOADED",
               resource=f"payment_order:{order.id}")
    db.commit()
    db.refresh(order)
    return order


@router.get("/api/admin/payment-orders", response_model=list[PaymentOrderOut])
def list_payment_orders_admin(
    status_filter: PaymentOrderStatus | None = None,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    stmt = select(PaymentOrder).order_by(PaymentOrder.created_at.desc()).limit(100)
    if status_filter is not None:
        stmt = stmt.where(PaymentOrder.status == status_filter)
    return db.execute(stmt).scalars().all()


def _activate_subscription(db: Session, order: PaymentOrder) -> None:
    if order.subscription_id is None:
        return
    sub = db.get(Subscription, order.subscription_id)
    if sub is None:
        return
    plan = db.get(Plan, order.plan_id)
    period_days = SUBSCRIPTION_PERIOD_DAYS.get(plan.billing_period.value if plan else "MONTHLY", 30)
    now = datetime.now(timezone.utc)
    base = sub.current_period_end if sub.current_period_end and sub.current_period_end > now else now
    sub.status = SubscriptionStatus.ACTIVE
    sub.current_period_end = base + timedelta(days=period_days)


@router.post("/api/admin/payment-orders/{order_id}/approve", response_model=PaymentOrderOut)
def approve_transfer(
    order_id: uuid.UUID,
    payload: ReviewTransferRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    order = db.get(PaymentOrder, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    if order.method != PaymentMethod.BANK_TRANSFER or order.status != PaymentOrderStatus.UNDER_REVIEW:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La orden no esta en revision.")

    order.status = PaymentOrderStatus.APPROVED
    order.reviewed_by_user_id = admin.id
    order.review_note = payload.note
    _activate_subscription(db, order)

    log_audit(db, actor_user_id=admin.id, tenant_id=order.tenant_id, action="PAYMENT_APPROVED",
               resource=f"payment_order:{order.id}", ip_address=safe_ip(request.client.host if request.client else None))
    db.commit()
    db.refresh(order)
    return order


@router.post("/api/admin/payment-orders/{order_id}/reject", response_model=PaymentOrderOut)
def reject_transfer(
    order_id: uuid.UUID,
    payload: ReviewTransferRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin_panel()),
):
    order = db.get(PaymentOrder, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    if order.method != PaymentMethod.BANK_TRANSFER or order.status != PaymentOrderStatus.UNDER_REVIEW:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La orden no esta en revision.")

    order.status = PaymentOrderStatus.REJECTED
    order.reviewed_by_user_id = admin.id
    order.review_note = payload.note

    log_audit(db, actor_user_id=admin.id, tenant_id=order.tenant_id, action="PAYMENT_REJECTED",
               resource=f"payment_order:{order.id}", ip_address=safe_ip(request.client.host if request.client else None))
    db.commit()
    db.refresh(order)
    return order


@router.post("/api/payments/bancard/webhook")
async def bancard_webhook(request: Request, db: Session = Depends(get_db)):
    """Callback publico de Bancard. Sin autenticacion de sesion (Bancard
    llama server-to-server) -- la unica autenticacion es recalcular el
    token MD5 con la private_key propia. Bancard tambien hace un ping de
    monitoreo cada 5 min con body vacio: debe responder 200 sin efectos."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    operation = body.get("operation")
    if not operation:
        # Ping de monitoreo (body vacio) u operacion sin datos: 200, sin
        # tocar nada.
        return {"status": "success"}

    shop_process_id = operation.get("shop_process_id")
    try:
        shop_process_id = int(shop_process_id)
    except (TypeError, ValueError):
        return {"status": "success"}

    order = db.execute(
        select(PaymentOrder).where(PaymentOrder.shop_process_id == shop_process_id)
    ).scalar_one_or_none()
    if order is None:
        return {"status": "success"}

    # Idempotencia: si ya se proceso esta orden, responder 200 sin
    # reprocesar (Bancard no expone un event_id propio, ver docs/PAYMENTS.md).
    if order.status in (PaymentOrderStatus.APPROVED, PaymentOrderStatus.REJECTED):
        return {"status": "success"}

    amount = Decimal(str(operation.get("amount", "0")))
    currency = Currency.PYG
    received_token = operation.get("token", "")

    if not bancard.verify_confirm_token(shop_process_id, amount, currency, received_token):
        # Token invalido: no se toca la orden. Se responde 200 igual
        # (Bancard cierra la conexion a los 60s si no hay 200) pero sin
        # aprobar nada -- el estado sigue AWAITING_CARD_CONFIRMATION.
        return {"status": "success"}

    if amount != order.amount:
        # El monto confirmado no coincide con lo que se le cobro al
        # cliente: nunca aprobar por confiar en el payload.
        return {"status": "success"}

    approved = operation.get("response") == "S"
    order.status = PaymentOrderStatus.APPROVED if approved else PaymentOrderStatus.REJECTED
    order.bancard_authorization_number = operation.get("authorization_number")
    order.bancard_response_code = operation.get("response_code")

    if approved:
        _activate_subscription(db, order)

    log_audit(db, actor_user_id=None, tenant_id=order.tenant_id,
               action="PAYMENT_APPROVED" if approved else "PAYMENT_REJECTED",
               resource=f"payment_order:{order.id}", metadata={"provider": "bancard"})
    db.commit()

    return {"status": "success"}
