"""CLI de bootstrap del SUPER_ADMIN. No hay registro publico -- este es el
UNICO camino, y debe negarse a crear un segundo SUPER_ADMIN automatico."""

import argparse

import pytest

from app import cli
from app.core.db import SessionLocal
from app.models.control_plane import User, UserSession
from app.security.roles import Role


@pytest.fixture(autouse=True)
def _cleanup_cli_users():
    yield
    db = SessionLocal()
    users = db.query(User).filter(User.email.like("pytest-cli-%")).all()
    ids = [u.id for u in users]
    db.query(UserSession).filter(UserSession.user_id.in_(ids)).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_create_superadmin_success(monkeypatch, capsys):
    inputs = iter(["pytest-cli-admin@example.com", "Admin De Prueba"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _="": "ClaveSegura123X")

    rc = cli.cmd_create_superadmin(argparse.Namespace())
    assert rc == 0

    out = capsys.readouterr().out
    assert "SUPER_ADMIN creado correctamente" in out
    assert "pytest-cli-admin@example.com" in out
    assert "ClaveSegura123X" not in out  # nunca se imprime la password

    db = SessionLocal()
    user = db.query(User).filter(User.email == "pytest-cli-admin@example.com").one()
    assert user.role == Role.SUPER_ADMIN
    assert user.password_hash != "ClaveSegura123X"
    db.close()


def test_create_superadmin_refuses_if_one_exists(monkeypatch, capsys):
    inputs = iter(["pytest-cli-admin2@example.com", "Otro Admin"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _="": "ClaveSegura123X")
    assert cli.cmd_create_superadmin(argparse.Namespace()) == 0

    inputs2 = iter(["pytest-cli-admin3@example.com", "Tercer Admin"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs2))
    rc = cli.cmd_create_superadmin(argparse.Namespace())
    assert rc == 1
    assert "Ya existe SUPER_ADMIN" in capsys.readouterr().out

    db = SessionLocal()
    assert db.query(User).filter(User.email == "pytest-cli-admin3@example.com").first() is None
    db.close()


def test_create_superadmin_rejects_weak_password(monkeypatch, capsys):
    inputs = iter(["pytest-cli-weak@example.com", "Alguien"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _="": "abc")

    rc = cli.cmd_create_superadmin(argparse.Namespace())
    assert rc == 1
    assert "invalida" in capsys.readouterr().out.lower()

    db = SessionLocal()
    assert db.query(User).filter(User.email == "pytest-cli-weak@example.com").first() is None
    db.close()


def test_create_superadmin_rejects_mismatched_confirmation(monkeypatch, capsys):
    inputs = iter(["pytest-cli-mismatch@example.com", "Alguien"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    passwords = iter(["ClaveSegura123X", "OtraClaveDistinta1", "ClaveSegura123X", "ClaveSegura123X"])
    monkeypatch.setattr("getpass.getpass", lambda _="": next(passwords))

    rc = cli.cmd_create_superadmin(argparse.Namespace())
    assert rc == 0  # la segunda ronda de prompts coincide y termina creando

    db = SessionLocal()
    assert db.query(User).filter(User.email == "pytest-cli-mismatch@example.com").first() is not None
    db.close()
