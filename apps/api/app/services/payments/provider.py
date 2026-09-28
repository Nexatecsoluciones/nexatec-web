"""Interfaz comun para proveedores de pago con tarjeta/checkout hospedado.
Bancard es la unica implementacion hoy; el proposito de esta interfaz es
que agregar un segundo proveedor (o reemplazar Bancard) no implique tocar
app/routers/payments.py, solo escribir un nuevo modulo que cumpla este
contrato.

La transferencia bancaria NO implementa esta interfaz a proposito: es un
flujo manual de revision humana, no un checkout online -- forzarla a este
mismo contrato agregaria una abstraccion que no aporta nada (ver
app/routers/payments.py para su flujo propio)."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.models.payments_enums import Currency


@dataclass(frozen=True)
class CheckoutResult:
    provider: str
    external_reference: str  # p.ej. process_id de Bancard
    checkout_data: dict  # lo que el frontend necesita para renderizar el checkout


class PaymentProvider(Protocol):
    async def create_checkout(
        self,
        shop_process_id: int,
        amount: Decimal,
        currency: Currency,
        description: str,
        return_url: str,
        cancel_url: str,
    ) -> CheckoutResult: ...
