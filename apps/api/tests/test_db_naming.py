"""Nombres de DB/rol seguros: ningun texto de usuario debe poder llegar
intacto (ni parcialmente) a un identificador usado en DDL."""

import uuid

import pytest

from app.models.tenancy_enums import Environment
from app.services.db_naming import (
    build_tenant_database_identifier,
    sanitize_identifier_component,
    assert_safe_identifier,
)

MALICIOUS_INPUTS = [
    "; DROP DATABASE nexatec_control; --",
    "robert'); DROP TABLE users;--",
    "nombre con espacios",
    "acentos ñ é ü café münchen",
    "a/b\\c",
    "quotes\"'`",
    "unicode raro 日本語 🎉",
    "a" * 300,
    "",
    "   ",
    "!!!$$$###",
]


@pytest.mark.parametrize("raw", MALICIOUS_INPUTS)
def test_sanitize_identifier_component_always_safe(raw):
    result = sanitize_identifier_component(raw)
    assert result != ""
    assert result[0].isalpha()
    assert len(result) <= 20
    assert all(c.isalnum() and c.isascii() or c == "_" for c in result)
    # No debe sobrevivir ningun caracter peligroso para SQL/shell.
    for dangerous in [";", "'", '"', "`", " ", "/", "\\", "--"]:
        assert dangerous not in result


def test_sanitize_identifier_component_is_deterministic():
    assert sanitize_identifier_component("Hola Mundo") == sanitize_identifier_component("Hola Mundo")


def test_build_tenant_database_identifier_is_safe_and_deterministic():
    tenant_id = uuid.uuid4()
    system_id = uuid.uuid4()

    demo_id = build_tenant_database_identifier(tenant_id, system_id, Environment.DEMO)
    prod_id = build_tenant_database_identifier(tenant_id, system_id, Environment.PRODUCTION)

    assert demo_id.startswith("nxt_")
    assert demo_id.endswith("_demo")
    assert prod_id.endswith("_prod")
    assert demo_id != prod_id
    assert len(demo_id) <= 63
    assert len(prod_id) <= 63

    # Determinista: mismos UUIDs -> mismo identificador.
    assert demo_id == build_tenant_database_identifier(tenant_id, system_id, Environment.DEMO)

    # No lanza al validarse a si mismo.
    assert_safe_identifier(demo_id)
    assert_safe_identifier(prod_id)


def test_build_tenant_database_identifier_never_uses_raw_uuid_dashes():
    tenant_id = uuid.uuid4()
    system_id = uuid.uuid4()
    identifier = build_tenant_database_identifier(tenant_id, system_id, Environment.DEMO)
    assert "-" not in identifier


def test_assert_safe_identifier_rejects_tampered_values():
    with pytest.raises(ValueError):
        assert_safe_identifier("nxt_ok; DROP TABLE users;--")
    with pytest.raises(ValueError):
        assert_safe_identifier("1_starts_with_digit")
    with pytest.raises(ValueError):
        assert_safe_identifier("a" * 100)
