"""Email transaccional (Brevo) sin red real: payload, best-effort ante
errores, nada sensible en logs, y cada disparador (recuperacion,
invitaciones, avisos de demo) enviando una sola vez."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, PasswordResetToken, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import DemoInstance, SystemAccess, TenantUser
from app.models.tenancy_enums import (
    Environment, ProvisioningStatus, SystemAccessStatus, TenantMemberRole, TenantMemberStatus, TenantStatus,
)
from app.routers.demos import sweep_expired_demos
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services import email as email_service


@pytest.fixture()
def brevo(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "brevo_api_key", "test-key")
    monkeypatch.setattr(s, "email_from_address", "no-reply@nexatecpy.com")
    monkeypatch.setattr(s, "public_base_url", "https://staging.nexatecpy.com")
    calls: list[httpx.Request] = []
    status = {"code": 201}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status["code"], json={"messageId": "x"})

    monkeypatch.setattr(email_service, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return {"calls": calls, "status": status}


def test_not_configured_sends_nothing(monkeypatch):
    monkeypatch.setattr(get_settings(), "brevo_api_key", "")
    monkeypatch.setattr(email_service, "_client", lambda: (_ for _ in ()).throw(AssertionError("no deberia llamar")))
    assert email_service.send_password_reset("a@example.com", "tok", 30) is False


def test_reset_email_payload(brevo, caplog):
    caplog.set_level(logging.DEBUG)
    assert email_service.send_password_reset("a@example.com", "TOKEN-SECRETO-123", 30) is True
    req = brevo["calls"][0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.brevo.com/v3/smtp/email"
    assert req.headers["api-key"] == "test-key"
    assert body["to"] == [{"email": "a@example.com"}]
    assert body["sender"]["email"] == "no-reply@nexatecpy.com"
    assert "https://staging.nexatecpy.com/restablecer?token=TOKEN-SECRETO-123" in body["htmlContent"]
    assert "TOKEN-SECRETO-123" not in caplog.text


def test_brevo_errors_never_raise_or_leak(brevo, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    brevo["status"]["code"] = 401
    assert email_service.send_invitation("a@example.com", "TOKEN-XYZ", 72, "Empresa") is False

    def boom(request):
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(email_service, "_client", lambda: httpx.Client(transport=httpx.MockTransport(boom)))
    assert email_service.send_invitation("a@example.com", "TOKEN-XYZ", 72) is False
    assert "TOKEN-XYZ" not in caplog.text


def test_html_escapes_company_name(brevo):
    email_service.send_invitation("a@example.com", "t", 72, "<script>alert(1)</script>")
    assert "<script>" not in json.loads(brevo["calls"][0].content)["htmlContent"]


@pytest.fixture()
def person():
    db = SessionLocal()
    u = User(email=f"mail-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("ClaveDePruebaSegura123"),
             role=Role.CLIENT_USER)
    db.add(u)
    db.commit()
    yield u
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == u.id).delete()
    db.query(User).filter(User.id == u.id).delete()
    db.commit()
    db.close()


def test_reset_request_sends_only_for_existing_users(brevo, person):
    c = TestClient(app)
    r1 = c.post("/api/auth/password-reset/request", json={"email": person.email, "turnstile_token": "dev"})
    r2 = c.post("/api/auth/password-reset/request", json={"email": "nadie-existe@example.com", "turnstile_token": "dev"})
    assert r1.json() == r2.json() == {"ok": True}  # misma respuesta: no revela si existe
    assert len(brevo["calls"]) == 1
    assert json.loads(brevo["calls"][0].content)["to"] == [{"email": person.email}]


@pytest.fixture()
def demo_tenant():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-mail-{sfx}", name="Mail", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"mail-{sfx}", legal_name="Mail SA", display_name="Empresa Mail", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    admin = User(email=f"mail-admin-{sfx}@example.com", password_hash=hash_password("ClaveDePruebaSegura123"),
                 role=Role.CLIENT_USER, tenant_id=tenant.id)
    viewer = User(email=f"mail-viewer-{sfx}@example.com", password_hash=hash_password("ClaveDePruebaSegura123"),
                  role=Role.CLIENT_USER, tenant_id=tenant.id)
    db.add_all([admin, viewer])
    db.commit()
    db.add_all([
        TenantUser(tenant_id=tenant.id, user_id=admin.id, role=TenantMemberRole.CLIENT_ADMIN, status=TenantMemberStatus.ACTIVE),
        TenantUser(tenant_id=tenant.id, user_id=viewer.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE),
    ])
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.DEMO,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=2, hours=12))
    db.add(access)
    db.commit()
    demo = DemoInstance(tenant_id=tenant.id, system_id=system.id, system_access_id=access.id, status=ProvisioningStatus.READY,
                        expires_at=access.expires_at)
    db.add(demo)
    db.commit()
    yield {"db": db, "access": access, "demo": demo, "admin": admin}
    db.query(DemoInstance).filter(DemoInstance.id == demo.id).delete()
    db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
    db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_([admin.id, viewer.id])).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


def test_demo_reminders_and_expiry_notice_sent_once_to_admins(brevo, demo_tenant):
    db, access, demo = demo_tenant["db"], demo_tenant["access"], demo_tenant["demo"]
    subjects = lambda: [json.loads(c.content)["subject"] for c in brevo["calls"]]  # noqa: E731
    recipients = lambda: {json.loads(c.content)["to"][0]["email"] for c in brevo["calls"]}  # noqa: E731

    sweep_expired_demos(db)
    assert subjects() == ["Tu demo de NEXATEC vence en 3 dias"]
    assert recipients() == {demo_tenant["admin"].email}  # solo administradores
    sweep_expired_demos(db)
    assert len(brevo["calls"]) == 1  # no se repite

    access.expires_at = datetime.now(timezone.utc) + timedelta(hours=20)
    db.commit()
    sweep_expired_demos(db)
    assert subjects()[-1] == "Tu demo de NEXATEC vence manana"
    sweep_expired_demos(db)
    assert len(brevo["calls"]) == 2

    access.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    sweep_expired_demos(db)
    assert subjects()[-1] == "Tu demo de NEXATEC vencio"
    db.refresh(demo)
    assert demo.expired_notice_sent_at is not None
    sweep_expired_demos(db)
    assert len(brevo["calls"]) == 3


def test_reminder_stays_pending_while_brevo_not_configured(monkeypatch, demo_tenant):
    monkeypatch.setattr(get_settings(), "brevo_api_key", "")
    sweep_expired_demos(demo_tenant["db"])
    demo_tenant["db"].refresh(demo_tenant["demo"])
    assert demo_tenant["demo"].reminder_3d_sent_at is None
