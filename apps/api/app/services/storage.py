"""Cliente S3 hacia Garage (self-hosted). El navegador nunca ve el host
interno ni credenciales: solo recibe URLs firmadas de corta duracion
generadas bajo demanda, siempre despues de verificar autorizacion
server-side (ver app/routers/media.py)."""

import uuid
from functools import lru_cache

import boto3
from botocore.client import Config as BotoConfig

from app.core.config import get_settings
from app.models.tenancy_enums import Environment

PRESIGNED_URL_TTL_SECONDS = 300


@lru_cache
def _client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_s3_endpoint,
        aws_access_key_id=settings.storage_s3_access_key,
        aws_secret_access_key=settings.storage_s3_secret_key,
        region_name=settings.storage_s3_region,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def build_object_key(
    tenant_id: uuid.UUID, environment: Environment, system_id: uuid.UUID | None, extension: str
) -> str:
    """Ruta segura y predecible: tenant-<hex>/[demo|prod]/<system-hex o
    'general'>/<uuid>.<ext>. Nunca se deriva del nombre de archivo
    original del usuario (evita path traversal y colisiones)."""
    system_part = system_id.hex[:8] if system_id else "general"
    env_part = "demo" if environment == Environment.DEMO else "prod"
    return f"tenant-{tenant_id.hex[:8]}/{env_part}/{system_part}/{uuid.uuid4().hex}.{extension}"


def upload_object(key: str, content: bytes, content_type: str) -> None:
    settings = get_settings()
    _client().put_object(Bucket=settings.storage_s3_bucket, Key=key, Body=content, ContentType=content_type)


def delete_object(key: str) -> None:
    settings = get_settings()
    _client().delete_object(Bucket=settings.storage_s3_bucket, Key=key)


def generate_presigned_get_url(key: str, expires_in: int = PRESIGNED_URL_TTL_SECONDS) -> str:
    """La firma se calcula contra el endpoint interno (el unico que boto3
    conoce), y solo se reescribe scheme+host al devolverla -- la
    firma SigV4 no depende del host para validar en Garage con
    addressing_style=path, asi que este swap es seguro. El proxy de
    Next.js (storage BFF) conecta al mismo endpoint interno, por lo que
    Garage ve exactamente la misma URL que se firmo."""
    settings = get_settings()
    raw_url = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.storage_s3_bucket, "Key": key},
        ExpiresIn=expires_in,
    )
    return raw_url.replace(settings.storage_s3_endpoint, settings.storage_public_base_url, 1)
