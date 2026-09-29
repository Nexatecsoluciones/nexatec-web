import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.control_center_enums import (
    DemoRequestStatus,
    JobStatus,
    JobType,
    ServiceHealthStatus,
    ServiceType,
)
from app.models.tenancy_enums import Environment


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class InstanceConfiguration(Base):
    """Una version de configuracion de tenant+sistema+entorno. Append-only
    a proposito: cada guardado crea una fila nueva con `version`
    incrementado, nunca se hace UPDATE sobre una version existente --
    asi el historial completo queda disponible sin una tabla separada, y
    "volver a una version anterior" es simplemente crear una version
    nueva copiando los datos de una vieja (nunca se borra ni se reescribe
    historia). `config`/`modules`/`branding` NUNCA contienen valores
    `secret` -- esos viven exclusivamente en InstanceSecretValue."""

    __tablename__ = "instance_configurations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "system_id", "environment", "version", name="uq_instance_config_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    system_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("systems.id"), nullable=False)
    environment: Mapped[Environment] = mapped_column(Enum(Environment, name="instance_config_environment"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    modules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    branding: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstanceSecretValue(Base):
    """Valores de campos tipo `secret` (API keys de integraciones de
    terceros, etc.), cifrados con la misma clave Fernet que las
    credenciales de DB de tenant (app/core/crypto.py). Ningun endpoint
    devuelve `encrypted_value`; la API solo puede confirmar si esta
    "Configurado" o no (ver app/routers/config_center.py)."""

    __tablename__ = "instance_secret_values"
    __table_args__ = (
        UniqueConstraint("tenant_id", "system_id", "environment", "key", name="uq_instance_secret_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    system_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("systems.id"), nullable=False)
    environment: Mapped[Environment] = mapped_column(Enum(Environment, name="instance_secret_environment"), nullable=False)
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ServiceRegistry(Base):
    """Registro de servicios que el gateway/health center puede conocer.
    `internal_target` SIEMPRE pasa por app/services/service_targets.py
    antes de guardarse -- solo SUPER_ADMIN puede escribir aca, y ni asi se
    acepta un destino fuera del allow-list (anti-SSRF). El cliente nunca
    ve `internal_target`, solo `public_hostname`."""

    __tablename__ = "service_registry"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[ServiceType] = mapped_column(Enum(ServiceType, name="registry_service_type"), nullable=False)
    environment: Mapped[Environment | None] = mapped_column(Enum(Environment, name="registry_environment"))
    public_hostname: Mapped[str | None] = mapped_column(String(255))
    internal_target: Mapped[str] = mapped_column(String(255), nullable=False)
    healthcheck_path: Mapped[str] = mapped_column(String(255), nullable=False, default="/")
    version: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[ServiceHealthStatus] = mapped_column(
        Enum(ServiceHealthStatus, name="registry_health_status"), nullable=False, default=ServiceHealthStatus.UNKNOWN
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DemoRequest(Base):
    """Solicitud publica de demo desde el catalogo. NO aprovisiona nada
    por si sola -- solo despues de que un admin la aprueba se crea el
    tenant/demo real (ver app/routers/demo_requests.py). Asi un visitante
    anonimo nunca puede crear infraestructura por su cuenta."""

    __tablename__ = "demo_requests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    company_name: Mapped[str | None] = mapped_column(String(200))
    system_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("systems.id"))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DemoRequestStatus] = mapped_column(
        Enum(DemoRequestStatus, name="demo_request_status"), nullable=False, default=DemoRequestStatus.NEW
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProvisioningJob(Base):
    """Registro de un job de provisioning con pasos REALES (nunca un
    porcentaje inventado). Hoy se ejecuta de forma sincrona dentro del
    mismo request (no existe todavia una cola/worker real -- ver
    docs/ARCHITECTURE.md); esta tabla deja el modelo listo para que, el
    dia que exista un worker async, solo cambie quien escribe los pasos,
    no como el frontend los lee."""

    __tablename__ = "provisioning_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[JobType] = mapped_column(Enum(JobType, name="job_type"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    system_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("systems.id"), nullable=False)
    environment: Mapped[Environment] = mapped_column(Enum(Environment, name="job_environment"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"), nullable=False, default=JobStatus.QUEUED)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
