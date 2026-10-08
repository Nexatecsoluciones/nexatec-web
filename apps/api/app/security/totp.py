"""TOTP (RFC 6238, HMAC-SHA1, 6 digitos, pasos de 30 s) con la libreria
estandar -- sin dependencias nuevas. Compatible con Google Authenticator,
Microsoft Authenticator, Authy, 1Password, etc."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
# Tolerancia de reloj: acepta el paso anterior y el siguiente (+-30 s).
WINDOW = 1


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret_b32: str) -> bytes:
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    return base64.b32decode(padded)


def code_at(secret_b32: str, step: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret_b32), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP_SECONDS)


def verify(secret_b32: str, code: str, last_used_step: int | None, now: float | None = None) -> int | None:
    """Devuelve el paso que valido (para guardarlo y evitar reuso) o None.
    Rechaza un paso <= al ultimo usado: el mismo codigo no sirve dos veces."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    base = current_step(now)
    for step in range(base - WINDOW, base + WINDOW + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(code_at(secret_b32, step), code):
            return step
    return None


def provisioning_uri(secret_b32: str, account: str, issuer: str = "NEXATEC") -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
