"""Verifica el algoritmo de firma de Bancard contra el documento oficial
(vPOS Compra Simple 0.3.1) -- no depende de red ni de credenciales
reales, es una funcion pura. Los valores esperados se calcularon a mano
siguiendo la formula documentada: md5(private_key + ...), concatenacion
directa sin separadores."""

import hashlib
from decimal import Decimal

from app.models.payments_enums import Currency
from app.services.payments.bancard import (
    _format_amount,
    _token_single_buy,
    token_confirm,
    token_get_confirmation,
    token_rollback,
    verify_confirm_token,
)


def test_format_amount_always_two_decimals_dot_separator():
    assert _format_amount(Decimal("10330")) == "10330.00"
    assert _format_amount(Decimal("130.5")) == "130.50"
    assert _format_amount(Decimal("99.999")) == "100.00"  # redondeo correcto


def test_single_buy_token_matches_documented_algorithm():
    private_key = "clave-privada-de-prueba-40-caracteres-x"
    expected = hashlib.md5(f"{private_key}54322{'10330.00'}PYG".encode()).hexdigest()
    assert _token_single_buy(private_key, 54322, "10330.00", "PYG") == expected


def test_confirm_token_includes_literal_confirm():
    private_key = "otra-clave-de-prueba"
    expected = hashlib.md5(f"{private_key}12313confirm10100.00PYG".encode()).hexdigest()
    assert token_confirm(private_key, 12313, "10100.00", "PYG") == expected


def test_get_confirmation_token():
    private_key = "clave-x"
    expected = hashlib.md5(f"{private_key}555get_confirmation".encode()).hexdigest()
    assert token_get_confirmation(private_key, 555) == expected


def test_rollback_token_uses_fixed_zero_amount():
    private_key = "clave-y"
    expected = hashlib.md5(f"{private_key}777rollback0.00".encode()).hexdigest()
    assert token_rollback(private_key, 777) == expected


def test_verify_confirm_token_without_configured_private_key_fails_closed(monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BANCARD_PRIVATE_KEY", "")
    get_settings.cache_clear()
    try:
        assert verify_confirm_token(123, Decimal("10.00"), Currency.PYG, "cualquier-token") is False
    finally:
        get_settings.cache_clear()


def test_verify_confirm_token_accepts_correctly_signed_and_rejects_tampered(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BANCARD_PRIVATE_KEY", "clave-test-verificacion")
    monkeypatch.setenv("BANCARD_PUBLIC_KEY", "public-test")
    get_settings.cache_clear()
    try:
        valid_token = token_confirm("clave-test-verificacion", 999, "500.00", "PYG")
        assert verify_confirm_token(999, Decimal("500.00"), Currency.PYG, valid_token) is True

        # Alguien intenta cambiar el monto confirmado sin recalcular el
        # hash correspondiente -- debe fallar.
        assert verify_confirm_token(999, Decimal("999999.00"), Currency.PYG, valid_token) is False
        assert verify_confirm_token(999, Decimal("500.00"), Currency.PYG, "token-inventado") is False
    finally:
        get_settings.cache_clear()
