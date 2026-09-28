"""Flujo completo de pagos (FASE 6): transferencia bancaria de punta a
punta (incluye subir un comprobante real via el storage de FASE 5),
webhook de Bancard con payload sintetico con la forma EXACTA documentada
por Bancard, calculo de monto siempre server-side, y aislamiento por
tenant sobre ordenes de pago."""

import io
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import Tenant, User, UserSession
from app.models.media import MediaAsset
from app.models.payments import PaymentOrder, Plan, Subscription
from app.models.payments_enums import BillingPeriod, Currency, PaymentOrderStatus, SubscriptionStatus
from app.models.tenancy import TenantUser
from app.models.tenancy_enums import TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.payments.bancard import token_confirm
from app.services.storage import delete_object

PASSWORD = "ClaveDePruebaSegura123"


def _login(email):
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"email": email, "password": PASSWORD, "turnstile_token": "dev"})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture()
def plan():
    db = SessionLocal()
    slug = f"plan-test-{uuid.uuid4().hex[:8]}"
    plan = Plan(
        slug=slug, name="Plan de prueba", price_amount=Decimal("550000.00"),
        price_currency=Currency.PYG, billing_period=BillingPeriod.MONTHLY,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    yield plan
    # El orden de teardown entre fixtures no esta garantizado: limpiar
    # cualquier fila que aun referencie este plan antes de borrarlo.
    db.query(PaymentOrder).filter(PaymentOrder.plan_id == plan.id).update({"transfer_proof_media_id": None})
    db.query(PaymentOrder).filter(PaymentOrder.plan_id == plan.id).delete(synchronize_session=False)
    db.query(Subscription).filter(Subscription.plan_id == plan.id).delete(synchronize_session=False)
    db.query(Plan).filter(Plan.id == plan.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def admin_client():
    db = SessionLocal()
    email = "pytest-pay-admin@example.com"
    existing = db.query(User).filter(User.email == email).one_or_none()
    if existing is not None:
        db.query(UserSession).filter(UserSession.user_id == existing.id).delete()
        db.query(User).filter(User.id == existing.id).delete()
        db.commit()
    user = User(email=email, password_hash=hash_password(PASSWORD), role=Role.SUPER_ADMIN)
    db.add(user)
    db.commit()
    client = _login(email)
    yield client
    # El orden de teardown entre fixtures no esta garantizado: puede
    # correr antes que el del tenant, que es quien borra el PaymentOrder
    # que referencia a este admin como revisor.
    db.query(PaymentOrder).filter(PaymentOrder.reviewed_by_user_id == user.id).update({"reviewed_by_user_id": None})
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def tenant_with_user():
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(slug=f"pay-t-{suffix}", legal_name="Pay Test SRL", display_name="Pay Test", status=TenantStatus.ACTIVE)
    db.add(tenant)
    db.flush()
    user = User(email=f"pay-user-{suffix}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant.id)
    db.add(user)
    db.flush()
    db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE))
    db.commit()

    yield {"tenant": tenant, "user": user, "client": _login(user.email)}

    db.query(PaymentOrder).filter(PaymentOrder.tenant_id == tenant.id).delete()
    db.query(Subscription).filter(Subscription.tenant_id == tenant.id).delete()
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.commit()
    db.close()


def _upload_fake_proof(client, tenant_id) -> str:
    img = Image.new("RGB", (100, 100), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    resp = client.post(
        "/api/media/upload", params={"tenant_id": tenant_id, "environment": "DEMO"},
        files={"file": ("comprobante.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_checkout_amount_is_computed_server_side_never_trusts_client(tenant_with_user, plan):
    tenant = tenant_with_user["tenant"]
    client = tenant_with_user["client"]

    resp = client.post(
        "/api/portal/checkout",
        json={"tenant_id": str(tenant.id), "plan_id": str(plan.id), "method": "BANK_TRANSFER"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # El endpoint no acepta ningun campo de monto en el request -- el
    # valor devuelto viene siempre del Plan.
    assert Decimal(body["order"]["amount"]) == plan.price_amount
    assert body["order"]["status"] == "PENDING_TRANSFER"


def test_bank_transfer_full_flow_upload_proof_and_admin_approve(tenant_with_user, plan, admin_client):
    tenant = tenant_with_user["tenant"]
    client = tenant_with_user["client"]

    checkout = client.post(
        "/api/portal/checkout",
        json={"tenant_id": str(tenant.id), "plan_id": str(plan.id), "method": "BANK_TRANSFER"},
    ).json()
    order_id = checkout["order"]["id"]

    media_id = _upload_fake_proof(client, str(tenant.id))

    proof_resp = client.post(f"/api/portal/payment-orders/{order_id}/proof", params={"media_asset_id": media_id})
    assert proof_resp.status_code == 200
    assert proof_resp.json()["status"] == "UNDER_REVIEW"

    # Un CLIENT_USER no puede aprobar su propio pago.
    self_approve = client.post(f"/api/admin/payment-orders/{order_id}/approve", json={})
    assert self_approve.status_code == 403

    approve = admin_client.post(f"/api/admin/payment-orders/{order_id}/approve", json={"note": "comprobante valido"})
    assert approve.status_code == 200
    assert approve.json()["status"] == "APPROVED"

    db = SessionLocal()
    sub = db.query(Subscription).filter(Subscription.tenant_id == tenant.id).one()
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.current_period_end is not None

    order = db.get(PaymentOrder, uuid.UUID(order_id))
    assert order.reviewed_by_user_id is not None
    assert order.review_note == "comprobante valido"

    media = db.get(MediaAsset, order.transfer_proof_media_id)
    delete_object(media.storage_key)
    if media.thumbnail_key:
        delete_object(media.thumbnail_key)
    order.transfer_proof_media_id = None
    db.flush()
    db.query(MediaAsset).filter(MediaAsset.id == media.id).delete()
    db.commit()
    db.close()


def test_cannot_upload_proof_to_someone_elses_order(tenant_with_user, plan):
    tenant_a = tenant_with_user["tenant"]
    client_a = tenant_with_user["client"]
    order = client_a.post(
        "/api/portal/checkout", json={"tenant_id": str(tenant_a.id), "plan_id": str(plan.id), "method": "BANK_TRANSFER"}
    ).json()["order"]

    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    tenant_b = Tenant(slug=f"pay-tb-{suffix}", legal_name="B", display_name="B", status=TenantStatus.ACTIVE)
    db.add(tenant_b)
    db.flush()
    user_b = User(email=f"pay-b-{suffix}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant_b.id)
    db.add(user_b)
    db.flush()
    db.add(TenantUser(tenant_id=tenant_b.id, user_id=user_b.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE))
    db.commit()
    client_b = _login(user_b.email)

    media_id_b = _upload_fake_proof(client_b, str(tenant_b.id))

    # Tenant B intenta subir SU comprobante a la orden de Tenant A.
    resp = client_b.post(f"/api/portal/payment-orders/{order['id']}/proof", params={"media_asset_id": media_id_b})
    assert resp.status_code == 404

    media_b = db.get(MediaAsset, uuid.UUID(media_id_b))
    delete_object(media_b.storage_key)
    if media_b.thumbnail_key:
        delete_object(media_b.thumbnail_key)
    db.query(MediaAsset).filter(MediaAsset.id == media_b.id).delete()
    db.query(UserSession).filter(UserSession.user_id == user_b.id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant_b.id).delete()
    db.query(User).filter(User.id == user_b.id).delete()
    db.query(Tenant).filter(Tenant.id == tenant_b.id).delete()
    db.commit()
    db.close()


def test_bancard_webhook_empty_monitoring_ping_is_noop():
    client = TestClient(app)
    resp = client.post("/api/payments/bancard/webhook", json={})
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}


def test_bancard_webhook_approves_order_with_valid_signature(monkeypatch, tenant_with_user, plan):
    from app.core.config import get_settings

    monkeypatch.setenv("BANCARD_PRIVATE_KEY", "clave-privada-webhook-test")
    monkeypatch.setenv("BANCARD_PUBLIC_KEY", "clave-publica-webhook-test")
    get_settings.cache_clear()

    tenant = tenant_with_user["tenant"]
    client = tenant_with_user["client"]

    db = SessionLocal()
    shop_process_id = 918273
    order = PaymentOrder(
        tenant_id=tenant.id, plan_id=plan.id, method="BANCARD_CARD",
        status=PaymentOrderStatus.AWAITING_CARD_CONFIRMATION,
        amount=Decimal("550000.00"), currency=Currency.PYG, shop_process_id=shop_process_id,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    order_id = order.id
    db.close()

    valid_token = token_confirm("clave-privada-webhook-test", shop_process_id, "550000.00", "PYG")

    webhook_payload = {
        "operation": {
            "token": valid_token,
            "shop_process_id": str(shop_process_id),
            "response": "S",
            "response_details": "aprobada",
            "currency": "PYG",
            "amount": "550000.00",
            "authorization_number": "123456",
            "ticket_number": "123456789123456",
            "response_code": "00",
            "response_description": "Transaccion aprobada.",
        }
    }

    try:
        resp = TestClient(app).post("/api/payments/bancard/webhook", json=webhook_payload)
        assert resp.status_code == 200

        db2 = SessionLocal()
        refreshed = db2.get(PaymentOrder, order_id)
        assert refreshed.status == PaymentOrderStatus.APPROVED
        assert refreshed.bancard_authorization_number == "123456"

        # Reenvio del mismo webhook (Bancard puede reintentar): idempotente,
        # no debe fallar ni volver a "procesar".
        resp2 = TestClient(app).post("/api/payments/bancard/webhook", json=webhook_payload)
        assert resp2.status_code == 200

        db2.query(PaymentOrder).filter(PaymentOrder.id == order_id).delete()
        db2.commit()
        db2.close()
    finally:
        get_settings.cache_clear()


def test_bancard_webhook_rejects_tampered_amount(monkeypatch, tenant_with_user, plan):
    from app.core.config import get_settings

    monkeypatch.setenv("BANCARD_PRIVATE_KEY", "otra-clave-privada")
    monkeypatch.setenv("BANCARD_PUBLIC_KEY", "otra-clave-publica")
    get_settings.cache_clear()

    tenant = tenant_with_user["tenant"]

    db = SessionLocal()
    shop_process_id = 555444
    order = PaymentOrder(
        tenant_id=tenant.id, plan_id=plan.id, method="BANCARD_CARD",
        status=PaymentOrderStatus.AWAITING_CARD_CONFIRMATION,
        amount=Decimal("100000.00"), currency=Currency.PYG, shop_process_id=shop_process_id,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    order_id = order.id
    db.close()

    # Token valido para 100000.00, pero el atacante manda amount=1.00 en
    # el body -- el token ya no corresponde a ese amount.
    valid_token_for_original_amount = token_confirm("otra-clave-privada", shop_process_id, "100000.00", "PYG")
    tampered_payload = {
        "operation": {
            "token": valid_token_for_original_amount,
            "shop_process_id": str(shop_process_id),
            "response": "S",
            "currency": "PYG",
            "amount": "1.00",
            "response_code": "00",
        }
    }

    try:
        resp = TestClient(app).post("/api/payments/bancard/webhook", json=tampered_payload)
        assert resp.status_code == 200  # Bancard exige 200 igual

        db2 = SessionLocal()
        refreshed = db2.get(PaymentOrder, order_id)
        # NUNCA debe quedar aprobada por un payload manipulado.
        assert refreshed.status == PaymentOrderStatus.AWAITING_CARD_CONFIRMATION
        db2.query(PaymentOrder).filter(PaymentOrder.id == order_id).delete()
        db2.commit()
        db2.close()
    finally:
        get_settings.cache_clear()
