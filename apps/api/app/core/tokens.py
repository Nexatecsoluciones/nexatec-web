import hashlib
import secrets


def new_raw_token(length_bytes: int = 32) -> str:
    return secrets.token_urlsafe(length_bytes)


def hash_token(raw_token: str) -> str:
    """Hash de tokens de un solo uso (sesion, reset de password). SHA-256
    es suficiente aca porque el token ya tiene alta entropia (no es una
    password elegida por un humano); Argon2id se reserva para passwords."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
