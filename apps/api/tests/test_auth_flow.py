"""Prueba de extremo a extremo del flujo de autenticacion: login fallido
generico, login correcto, sesion valida, logout con revocacion real y
bloqueo progresivo por fuerza bruta. Corre contra la base nexatec_control
ya migrada (ver .env); limpia sus propios datos al terminar."""

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import User, UserSession
from app.security.passwords import hash_password
from app.security.roles import Role

TEST_EMAIL = "pytest-auth@example.com"
TEST_PASSWORD = "ClaveDePruebaSegura123"


@pytest.fixture()
def test_user():
    db = SessionLocal()
    db.query(UserSession).filter(
        UserSession.user_id.in_(
            db.query(User.id).filter(User.email == TEST_EMAIL)
        )
    ).delete(synchronize_session=False)
    db.query(User).filter(User.email == TEST_EMAIL).delete()
    db.commit()

    user = User(
        email=TEST_EMAIL,
        password_hash=hash_password(TEST_PASSWORD),
        role=Role.CLIENT_USER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user

    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def client():
    return TestClient(app)


def test_login_wrong_password_returns_generic_401(client, test_user):
    resp = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": "incorrecta", "turnstile_token": "dev"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Email o contrasena incorrectos."


def test_login_unknown_email_returns_same_generic_401(client):
    resp = client.post(
        "/api/auth/login",
        json={"email": "no-existe@example.com", "password": "x", "turnstile_token": "dev"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Email o contrasena incorrectos."


def test_full_login_session_logout_cycle(client, test_user):
    resp = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD, "turnstile_token": "dev"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"email": TEST_EMAIL, "role": "CLIENT_USER", "next": "FULL"}
    assert "nexatec_session" in resp.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == TEST_EMAIL

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    me_after_logout = client.get("/api/auth/me")
    assert me_after_logout.status_code == 401


def test_me_without_session_is_401(client):
    resp = TestClient(app).get("/api/auth/me")
    assert resp.status_code == 401


def test_progressive_lockout_after_repeated_failures(client, test_user):
    for _ in range(5):
        client.post(
            "/api/auth/login",
            json={"email": TEST_EMAIL, "password": "incorrecta", "turnstile_token": "dev"},
        )
    # Al 5to intento fallido se activa el bloqueo temporal (ver
    # _LOCKOUT_MINUTES_BY_ATTEMPT en app/routers/auth.py).
    resp = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD, "turnstile_token": "dev"},
    )
    assert resp.status_code == 429
