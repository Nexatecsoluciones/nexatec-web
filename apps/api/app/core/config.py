from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracion central. Pydantic falla al arrancar si falta una
    variable obligatoria (sin valores de fallback inseguros)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    nexatec_env: Literal["development", "test", "staging", "production"] = Field(
        alias="NEXATEC_ENV"
    )

    control_db_host: str = Field(alias="NEXATEC_CONTROL_DB_HOST")
    control_db_port: int = Field(alias="NEXATEC_CONTROL_DB_PORT")
    control_db_name: str = Field(alias="NEXATEC_CONTROL_DB_NAME")
    control_db_user: str = Field(alias="NEXATEC_CONTROL_DB_USER")
    control_db_password: str = Field(alias="NEXATEC_CONTROL_DB_PASSWORD")

    # Rol de PostgreSQL separado, usado EXCLUSIVAMENTE por el servicio de
    # provisioning (crear DB/rol por tenant+sistema+entorno). El rol de la
    # app normal (control_db_user de arriba) no tiene CREATEDB/CREATEROLE.
    # Ver docs/ARCHITECTURE.md seccion "Provisioning".
    provisioner_db_host: str = Field(alias="NEXATEC_PROVISIONER_DB_HOST")
    provisioner_db_port: int = Field(alias="NEXATEC_PROVISIONER_DB_PORT")
    provisioner_db_user: str = Field(alias="NEXATEC_PROVISIONER_DB_USER")
    provisioner_db_password: str = Field(alias="NEXATEC_PROVISIONER_DB_PASSWORD")

    # Clave simetrica (Fernet) para cifrar passwords de DBs de tenant en
    # reposo. Generar con: python -c "from cryptography.fernet import
    # Fernet; print(Fernet.generate_key().decode())"
    db_credentials_encryption_key: str = Field(alias="NEXATEC_DB_CREDENTIALS_ENCRYPTION_KEY")

    api_port: int = Field(alias="NEXATEC_API_PORT")

    session_secret: str = Field(alias="NEXATEC_SESSION_SECRET", min_length=32)
    session_cookie_name: str = Field(alias="NEXATEC_SESSION_COOKIE_NAME")
    session_max_age_seconds: int = Field(alias="NEXATEC_SESSION_MAX_AGE_SECONDS")

    cors_allowed_origins: str = Field(alias="NEXATEC_CORS_ALLOWED_ORIGINS")

    turnstile_site_key: str = Field(default="", alias="NEXATEC_TURNSTILE_SITE_KEY")
    turnstile_secret_key: str = Field(default="", alias="NEXATEC_TURNSTILE_SECRET_KEY")

    # Storage S3-compatible (Garage, self-hosted -- MinIO Community quedo
    # archivado/sin mantenimiento, ver docs/ARCHITECTURE.md). Nunca se
    # expone host/credenciales al cliente: la API genera URLs firmadas de
    # corta duracion bajo demanda.
    storage_s3_endpoint: str = Field(alias="NEXATEC_STORAGE_S3_ENDPOINT")
    storage_s3_region: str = Field(alias="NEXATEC_STORAGE_S3_REGION")
    storage_s3_bucket: str = Field(alias="NEXATEC_STORAGE_S3_BUCKET")
    storage_s3_access_key: str = Field(alias="NEXATEC_STORAGE_S3_ACCESS_KEY")
    storage_s3_secret_key: str = Field(alias="NEXATEC_STORAGE_S3_SECRET_KEY")
    # URL publica desde la que un navegador puede alcanzar el storage. En
    # dev, igual al endpoint interno (todo esta en localhost). En
    # staging/production, pasa por el BFF de Next.js
    # (apps/web/src/app/storage/[...path]/route.ts) para que el navegador
    # nunca necesite conocer 127.0.0.1:3900. Las URLs firmadas se generan
    # contra el endpoint interno y solo se les reescribe el scheme+host,
    # nunca la firma/query string -- asi la firma sigue siendo valida.
    storage_public_base_url: str = Field(alias="NEXATEC_STORAGE_PUBLIC_BASE_URL")

    @field_validator("cors_allowed_origins")
    @classmethod
    def no_wildcard_cors(cls, v: str) -> str:
        if v.strip() == "*":
            raise ValueError(
                "NEXATEC_CORS_ALLOWED_ORIGINS no puede ser '*'; "
                "listar origenes explicitos separados por comas."
            )
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def control_db_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.control_db_user}:{self.control_db_password}"
            f"@{self.control_db_host}:{self.control_db_port}/{self.control_db_name}"
        )

    @property
    def provisioner_maintenance_db_url(self) -> str:
        """Conexion del rol de provisioning a la DB de mantenimiento
        `postgres` (requerida para poder ejecutar CREATE DATABASE, que no
        puede correr dentro de una transaccion contra la DB que se crea)."""
        return (
            f"postgresql+psycopg://{self.provisioner_db_user}:{self.provisioner_db_password}"
            f"@{self.provisioner_db_host}:{self.provisioner_db_port}/postgres"
        )

    @property
    def is_development(self) -> bool:
        return self.nexatec_env == "development"

    @property
    def is_production(self) -> bool:
        """Endurecimiento de seguridad (cookies Secure, ocultar /docs,
        HSTS): se aplica en 'staging' igual que en 'production'. Staging
        se sirve por HTTPS real (Cloudflare Tunnel) y no es un entorno de
        desarrollo, aunque el nombre lo sugiera -- por eso el bypass de
        Turnstile NO usa esta propiedad (ver app/security/turnstile.py),
        que exige exactamente NEXATEC_ENV=development."""
        return self.nexatec_env in ("staging", "production")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
