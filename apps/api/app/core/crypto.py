"""Cifrado simetrico para las passwords de las DBs de tenant (ver
app/models/tenancy.py::TenantDatabaseCredential). Esto es una solucion
intermedia, no un secret manager: la clave vive en una variable de entorno
del proceso de la API, no en un vault con rotacion/auditoria propia.

Estrategia futura (documentada, no implementada todavia): migrar a un
secret manager real (HashiCorp Vault, AWS Secrets Manager o equivalente
self-hosted) cuando el numero de tenants/production instances lo
justifique; en ese momento esta funcion pasa a ser un adapter que llama
al secret manager en vez de descifrar localmente, sin cambiar quienes la
consumen (TenantDatabaseManager / ProvisioningService)."""

from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    return Fernet(settings.db_credentials_encryption_key.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
