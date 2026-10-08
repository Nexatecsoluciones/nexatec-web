"""Ajustes SOLO para la corrida de tests. No van en apps/api/.env porque el
servicio de staging (WorkingDirectory=apps/api) usa ese .env como respaldo
de cualquier variable que su EnvironmentFile no defina."""

import os

# Los tests NUNCA usan la base del control plane de staging: tienen la suya
# (mismo dueno nexatec_app, sin acceso publico). Un test que falla a mitad
# no puede dejar datos visibles en el Control Center real.
os.environ["NEXATEC_CONTROL_DB_NAME"] = os.environ.get("NEXATEC_TEST_CONTROL_DB_NAME", "nexatec_control_test")

# Los tests de administracion no configuran MFA; tests/test_mfa.py prueba la
# exigencia activandola explicitamente.
os.environ.setdefault("NEXATEC_REQUIRE_ADMIN_MFA", "false")


def pytest_sessionstart(session):
    """La base de tests siempre al dia con las migraciones del control plane."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from app.core.config import get_settings

    assert get_settings().control_db_name != "nexatec_control", "los tests no pueden correr contra la base de staging"
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")
