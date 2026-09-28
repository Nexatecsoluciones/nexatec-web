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

    api_port: int = Field(alias="NEXATEC_API_PORT")

    session_secret: str = Field(alias="NEXATEC_SESSION_SECRET", min_length=32)
    session_cookie_name: str = Field(alias="NEXATEC_SESSION_COOKIE_NAME")
    session_max_age_seconds: int = Field(alias="NEXATEC_SESSION_MAX_AGE_SECONDS")

    cors_allowed_origins: str = Field(alias="NEXATEC_CORS_ALLOWED_ORIGINS")

    turnstile_site_key: str = Field(default="", alias="NEXATEC_TURNSTILE_SITE_KEY")
    turnstile_secret_key: str = Field(default="", alias="NEXATEC_TURNSTILE_SECRET_KEY")

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
    def is_production(self) -> bool:
        return self.nexatec_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
