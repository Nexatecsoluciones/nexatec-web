"""Integracion con Bancard vPOS (Compra Simple, API 0.3). Nombres de
campos y algoritmo de firma tomados de la documentacion oficial de
Bancard ("Integracion con eCommerce Bancard - Compra Simple v0.3.1"), no
inventados -- ver docs/PAYMENTS.md para las citas y fuentes exactas.

NUNCA se captura numero de tarjeta/CVV/vencimiento en este servidor: el
formulario de tarjeta lo sirve Bancard (iframe/redirect hospedado), este
codigo solo inicia la compra y valida la confirmacion.
"""

import hashlib
from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.core.config import get_settings
from app.models.payments_enums import Currency

_BASE_URLS = {
    "staging": "https://vpos.infonet.com.py:8888",
    "production": "https://vpos.infonet.com.py",
}


class BancardNotConfiguredError(Exception):
    """No hay cuenta de comercio Bancard real configurada todavia."""


class BancardApiError(Exception):
    def __init__(self, key: str, message: str):
        super().__init__(f"{key}: {message}")
        self.key = key


@dataclass(frozen=True)
class SingleBuyResult:
    process_id: str
    shop_process_id: int


def _format_amount(amount: Decimal) -> str:
    """Bancard exige dos decimales exactos y punto como separador, tanto
    en el JSON como en el hash -- el mismo string en ambos lugares."""
    return f"{amount.quantize(Decimal('0.01')):.2f}"


def _require_config() -> tuple[str, str, str]:
    settings = get_settings()
    if not settings.bancard_public_key or not settings.bancard_private_key:
        raise BancardNotConfiguredError(
            "Bancard no esta configurado todavia (faltan BANCARD_PUBLIC_KEY/BANCARD_PRIVATE_KEY)."
        )
    return settings.bancard_public_key, settings.bancard_private_key, _BASE_URLS[settings.bancard_env]


def _token_single_buy(private_key: str, shop_process_id: int, amount_str: str, currency: str) -> str:
    # md5(private_key + shop_process_id + amount + currency) -- orden
    # exacto documentado por Bancard.
    raw = f"{private_key}{shop_process_id}{amount_str}{currency}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def token_confirm(private_key: str, shop_process_id: int, amount_str: str, currency: str) -> str:
    # md5(private_key + shop_process_id + "confirm" + amount + currency)
    raw = f"{private_key}{shop_process_id}confirm{amount_str}{currency}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def token_get_confirmation(private_key: str, shop_process_id: int) -> str:
    raw = f"{private_key}{shop_process_id}get_confirmation"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def token_rollback(private_key: str, shop_process_id: int) -> str:
    # El token de confirm para un rollback usa "0.00" fijo como amount,
    # segun la documentacion oficial.
    raw = f"{private_key}{shop_process_id}rollback0.00"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def create_single_buy(
    shop_process_id: int,
    amount: Decimal,
    description: str,
    return_url: str,
    cancel_url: str,
    currency: Currency = Currency.PYG,
) -> SingleBuyResult:
    public_key, private_key, base_url = _require_config()
    amount_str = _format_amount(amount)
    token = _token_single_buy(private_key, shop_process_id, amount_str, currency.value)

    payload = {
        "public_key": public_key,
        "operation": {
            "token": token,
            "shop_process_id": shop_process_id,
            "currency": currency.value,
            "amount": amount_str,
            "description": description[:100],
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{base_url}/vpos/api/0.3/single_buy", json=payload)
        data = resp.json()

    if data.get("status") != "success":
        messages = data.get("messages", [{}])
        first = messages[0] if messages else {}
        raise BancardApiError(first.get("key", "UnknownError"), first.get("dsc", str(data)))

    return SingleBuyResult(process_id=data["process_id"], shop_process_id=shop_process_id)


def verify_confirm_token(shop_process_id: int, amount: Decimal, currency: Currency, received_token: str) -> bool:
    """Unica forma de autenticar el webhook de Bancard: no hay header de
    firma ni IP allowlist documentada, solo recalcular este hash con la
    private_key propia y compararlo -- si amount/currency fueron
    manipulados en transito, el hash no va a coincidir."""
    settings = get_settings()
    if not settings.bancard_private_key:
        return False
    expected = token_confirm(settings.bancard_private_key, shop_process_id, _format_amount(amount), currency.value)
    return expected == received_token
