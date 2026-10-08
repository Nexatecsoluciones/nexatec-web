"""Login por etapas: cambio obligatorio de contrasena, MFA TOTP (alta,
verificacion, anti-reuso, bloqueo por intentos) y exigencia de MFA para el
personal de NEXATEC en el Control Center."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, SecurityEvent, User, UserSession
from app.security import totp
from app.security.passwords import hash_password
from app.security.roles import Role

PASSWORD = "ClaveDePruebaSegura123"
NEW_PASSWORD = "OtraClaveMuySegura456"


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password, "turnstile_token": "dev"})


@pytest.fixture()
def make_user():
    created = []

    def _make(role=Role.SUPER_ADMIN, must_change=False):
        db = SessionLocal()
        u = User(email=f"mfa-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password(PASSWORD),
                 role=role, must_change_password=must_change)
        db.add(u)
        db.commit()
        db.refresh(u)
        db.close()
        created.append(u.id)
        return u

    yield _make
    db = SessionLocal()
    db.query(UserSession).filter(UserSession.user_id.in_(created)).delete(synchronize_session=False)
    db.query(SecurityEvent).filter(SecurityEvent.user_id.in_(created)).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.actor_user_id.in_(created)).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(created)).delete(synchronize_session=False)
    db.commit()
    db.close()


def _secret(user_id) -> str:
    db = SessionLocal()
    s = decrypt_secret(db.get(User, user_id).mfa_totp_secret)
    db.close()
    return s


def _enable_mfa(client: TestClient, user) -> str:
    setup = client.post("/api/auth/mfa/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/NEXATEC")
    db = SessionLocal()
    stored = db.get(User, user.id).mfa_totp_secret
    db.close()
    assert stored != setup["secret"]  # guardado cifrado, no en claro
    code = totp.code_at(setup["secret"], totp.current_step())
    assert client.post("/api/auth/mfa/enable", json={"code": code}).json() == {"mfa_enabled": True}
    return setup["secret"]


def test_forced_password_change_blocks_everything_until_done(make_user):
    u = make_user(role=Role.CLIENT_USER, must_change=True)
    other = TestClient(app)
    c = TestClient(app)
    assert _login(c, u.email).json()["next"] == "PASSWORD_CHANGE"
    r = c.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["detail"] == "STEP_REQUIRED:PASSWORD_CHANGE"
    assert c.get("/api/portal/my-systems").status_code == 401

    assert c.post("/api/auth/change-password", json={"current_password": "mal", "new_password": NEW_PASSWORD}).status_code == 400
    assert c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": PASSWORD}).status_code == 400
    assert c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "123"}).status_code == 400
    # Otra sesion abierta antes del cambio: tiene que quedar revocada.
    assert _login(other, u.email).json()["next"] == "PASSWORD_CHANGE"
    ok = c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert ok.status_code == 200 and ok.json()["next"] == "FULL"
    assert c.get("/api/auth/me").status_code == 200
    assert other.post("/api/auth/change-password", json={"current_password": NEW_PASSWORD, "new_password": "X" + NEW_PASSWORD}).status_code == 401
    assert _login(TestClient(app), u.email, NEW_PASSWORD).json()["next"] == "FULL"


def test_mfa_enrollment_and_login_second_step(make_user):
    u = make_user(role=Role.CLIENT_USER)
    c = TestClient(app)
    assert _login(c, u.email).json()["next"] == "FULL"
    secret = _enable_mfa(c, u)
    assert c.get("/api/auth/me").json()["mfa_enabled"] is True

    c2 = TestClient(app)
    assert _login(c2, u.email).json()["next"] == "MFA"
    assert c2.get("/api/auth/me").json()["detail"] == "STEP_REQUIRED:MFA"
    assert c2.post("/api/auth/mfa/verify", json={"code": "000000"}).status_code == 400
    # Codigo valido pero del paso ya usado al activar: rechazado (anti reuso).
    assert c2.post("/api/auth/mfa/verify", json={"code": totp.code_at(secret, totp.current_step())}).status_code == 400
    nxt = totp.code_at(secret, totp.current_step() + 1)
    assert c2.post("/api/auth/mfa/verify", json={"code": nxt}).json()["next"] == "FULL"
    assert c2.get("/api/auth/me").status_code == 200


def test_mfa_too_many_wrong_codes_revokes_pending_session(make_user):
    u = make_user(role=Role.CLIENT_USER)
    c = TestClient(app)
    _login(c, u.email)
    secret = _enable_mfa(c, u)
    c2 = TestClient(app)
    _login(c2, u.email)
    for _ in range(4):
        assert c2.post("/api/auth/mfa/verify", json={"code": "111111"}).status_code == 400
    assert c2.post("/api/auth/mfa/verify", json={"code": "111111"}).status_code == 401
    # Ni con el codigo correcto: hay que volver a poner la contrasena.
    assert c2.post("/api/auth/mfa/verify", json={"code": totp.code_at(secret, totp.current_step() + 1)}).status_code == 401


def test_mfa_comes_before_forced_password_change(make_user):
    u = make_user(role=Role.CLIENT_USER)
    c = TestClient(app)
    _login(c, u.email)
    secret = _enable_mfa(c, u)
    db = SessionLocal()
    db.get(User, u.id).must_change_password = True
    db.commit()
    db.close()
    c2 = TestClient(app)
    assert _login(c2, u.email).json()["next"] == "MFA"
    assert c2.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 401
    r = c2.post("/api/auth/mfa/verify", json={"code": totp.code_at(secret, totp.current_step() + 1)})
    assert r.json()["next"] == "PASSWORD_CHANGE"


def test_admin_panel_requires_mfa_when_enforced(make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "require_admin_mfa", True)
    u = make_user(role=Role.SUPER_ADMIN)
    c = TestClient(app)
    assert _login(c, u.email).json()["next"] == "FULL"
    r = c.get("/api/admin/tenants")
    assert r.status_code == 403 and r.json()["detail"] == "MFA_REQUIRED"
    assert c.get("/api/auth/me").json()["mfa_required"] is True
    _enable_mfa(c, u)
    assert c.get("/api/admin/tenants").status_code == 200
    # El personal de NEXATEC no puede apagarse el MFA.
    assert c.post("/api/auth/mfa/disable", json={"password": PASSWORD, "code": "123456"}).status_code == 403


def test_totp_rejects_malformed_codes():
    s = totp.generate_secret()
    for bad in ("", "12345", "abcdef", "1234567", None):
        assert totp.verify(s, bad, None) is None
