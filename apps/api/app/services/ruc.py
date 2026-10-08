"""Validacion de FORMATO de RUC paraguayo (digito verificador modulo 11).

Esto NO verifica que el RUC exista ni a quien pertenece -- solo que el DV
es consistente con el numero. Un RUC que pasa esta validacion queda como
FORMAT_OK, nunca como VERIFIED_PROVIDER (eso requiere un proveedor
autorizado, ver docs/ERP_DATA_MODEL.md)."""

import re

_RUC_BASE_RE = re.compile(r"^\d{1,8}$")


def compute_dv(ruc_base: str, base_max: int = 11) -> int:
    """Algoritmo modulo 11 usado para el DV del RUC en Paraguay: se
    recorren los digitos de derecha a izquierda multiplicando por 2..11
    (ciclico), y el DV es 11 - (suma % 11), o 0 si el resto es 0 o 1."""
    if not _RUC_BASE_RE.match(ruc_base):
        raise ValueError("El RUC base debe ser solo digitos (1 a 8).")
    total = 0
    k = 2
    for digit in reversed(ruc_base):
        total += int(digit) * k
        k = 2 if k >= base_max else k + 1
    remainder = total % 11
    return 11 - remainder if remainder > 1 else 0


def is_valid(ruc_base: str, dv: str) -> bool:
    if not dv or not dv.isdigit() or len(dv) != 1:
        return False
    try:
        return compute_dv(ruc_base) == int(dv)
    except ValueError:
        return False
