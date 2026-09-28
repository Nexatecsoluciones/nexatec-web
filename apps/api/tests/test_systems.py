"""RBAC del catalogo de sistemas: lectura publica sin auth, administracion
(listar todos / crear) exclusiva de roles admin, resuelto siempre
server-side. Limpia sus propios datos al terminar."""

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import User, UserSession
from app.models.system import System
from app.security.passwords import hash_password
from app.security.roles import Role

ADMIN_EMAIL = "pytest-admin@example.com"
CLIENT_EMAIL = "pytest-client@example.com"
PASSWORD = "ClaveDePruebaSegura123"


def _make_user(db, email, role):
    db.query(UserSession).filter(
        UserSession.user_id.in_(db.query(User.id).filter(User.email == email))
    ).delete(synchronize_session=False)
    db.query(User).filter(User.email == email).delete()
    db.commit()
    user = User(email=email, password_hash=hash_password(PASSWORD), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def admin_client():
    db = SessionLocal()
    user = _make_user(db, ADMIN_EMAIL, Role.SUPER_ADMIN)
    client = TestClient(app)
    client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": PASSWORD, "turnstile_token": "dev"},
    )
    yield client
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def client_user_client():
    db = SessionLocal()
    user = _make_user(db, CLIENT_EMAIL, Role.CLIENT_USER)
    client = TestClient(app)
    client.post(
        "/api/auth/login",
        json={"email": CLIENT_EMAIL, "password": PASSWORD, "turnstile_token": "dev"},
    )
    yield client
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    db.close()


@pytest.fixture()
def cleanup_test_system():
    yield
    db = SessionLocal()
    db.query(System).filter(System.slug == "sistema-de-prueba").delete()
    db.commit()
    db.close()


def test_public_list_does_not_require_auth():
    resp = TestClient(app).get("/api/systems")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_admin_list_rejects_anonymous():
    resp = TestClient(app).get("/api/admin/systems")
    assert resp.status_code == 401


def test_admin_list_rejects_client_role(client_user_client):
    resp = client_user_client.get("/api/admin/systems")
    assert resp.status_code == 403


def test_admin_can_create_system(admin_client, cleanup_test_system):
    resp = admin_client.post(
        "/api/admin/systems",
        json={
            "slug": "sistema-de-prueba",
            "name": "Sistema de Prueba",
            "short_description": "Solo para test automatizado.",
            "category": "Test",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "sistema-de-prueba"
    # Defaults del schema, no lo que el cliente no envio:
    assert body["is_active"] is True


def test_client_role_cannot_create_system(client_user_client):
    resp = client_user_client.post(
        "/api/admin/systems",
        json={
            "slug": "otro-sistema",
            "name": "Otro",
            "short_description": "x",
            "category": "x",
        },
    )
    assert resp.status_code == 403


def test_create_duplicate_slug_conflicts(admin_client, cleanup_test_system):
    payload = {
        "slug": "sistema-de-prueba",
        "name": "Sistema de Prueba",
        "short_description": "Solo para test automatizado.",
        "category": "Test",
    }
    first = admin_client.post("/api/admin/systems", json=payload)
    assert first.status_code == 201
    second = admin_client.post("/api/admin/systems", json=payload)
    assert second.status_code == 409
