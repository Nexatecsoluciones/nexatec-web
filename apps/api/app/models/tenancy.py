import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.tenancy_enums import (
    Environment,
    ProvisioningStatus,
    SystemAccessStatus,
    TenantMemberRole,
    TenantMemberStatus,
)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TenantUser(Base):
    """Membership explicita: un usuario puede pertenecer a mas de un tenant.
    El rol/estado AQUI es el que manda para autorizacion dentro del tenant;
    User.role (global) solo aplica a personal de NEXATEC sin tenant."""

    __tablename__ = "tenant_users"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_users_tenant_user"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    role: Mapped[TenantMemberRole] = mapped_column(
        Enum(TenantMemberRole, name="tenant_member_role"), nullable=False
    )
    status: Mapped[TenantMemberStatus] = mapped_column(
        Enum(TenantMemberStatus, name="tenant_member_status"),
        nullable=False,
        default=TenantMemberStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SystemAccess(Base):
    """Entitlement: a que sistema, en que entorno, tiene acceso un tenant.
    Esto es lo unico que decide que aparece en /portal -- nunca se infiere
    del catalogo publico de `systems`."""

    __tablename__ = "system_access"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "system_id", "environment", name="uq_system_access_tenant_system_env"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("systems.id"), nullable=False, index=True
    )
    environment: Mapped[Environment] = mapped_column(
        Enum(Environment, name="access_environment"), nullable=False
    )
    status: Mapped[SystemAccessStatus] = mapped_column(
        Enum(SystemAccessStatus, name="system_access_status"),
        nullable=False,
        default=SystemAccessStatus.PENDING,
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_users: Mapped[int | None] = mapped_column(Integer)
    storage_limit_mb: Mapped[int | None] = mapped_column(Integer)
    request_limit: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantDatabase(Base):
    """Registro central: donde vive realmente la base de un tenant+sistema
    +entorno. Todo acceso a datos de tenant pasa por esta tabla, nunca por
    un host/puerto hardcodeado en el codigo de negocio.

    NUNCA exponer database_host_ref/database_user_ref/database_name crudos
    a un usuario normal via API -- ver SystemAccessOut / los routers, que
    jamas serializan este modelo directamente."""

    __tablename__ = "tenant_databases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "system_id", "environment", name="uq_tenant_db_tenant_system_env"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("systems.id"), nullable=False, index=True
    )
    environment: Mapped[Environment] = mapped_column(
        Enum(Environment, name="tenant_db_environment"), nullable=False
    )
    # Identificador logico y seguro (deriva de UUIDs en hex, nunca de texto
    # de usuario). Es el mismo valor usado como nombre real de DB y de rol.
    database_identifier: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    # Referencia logica al host/instancia (hoy siempre "native-postgres";
    # permite migrar un tenant a una instancia dedicada despues cambiando
    # solo esta fila, ver docs/ARCHITECTURE.md).
    database_host_ref: Mapped[str] = mapped_column(String(80), nullable=False, default="native-postgres")
    database_name: Mapped[str] = mapped_column(String(63), nullable=False)
    database_user_ref: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[ProvisioningStatus] = mapped_column(
        Enum(ProvisioningStatus, name="tenant_db_status"),
        nullable=False,
        default=ProvisioningStatus.REQUESTED,
    )
    schema_version: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    credential: Mapped["TenantDatabaseCredential | None"] = relationship(
        back_populates="tenant_database", uselist=False
    )


class TenantDatabaseCredential(Base):
    """Password de la DB del tenant, cifrada (ver app/core/crypto.py). Tabla
    separada a proposito: ningun listado/join casual de TenantDatabase la
    trae, y ningun schema Pydantic de salida la referencia. Estrategia
    futura: mover esto a un secret manager (Vault/AWS Secrets Manager/etc)
    cuando el volumen lo justifique; documentado en docs/ARCHITECTURE.md."""

    __tablename__ = "tenant_database_credentials"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant_databases.id"), unique=True, nullable=False
    )
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    tenant_database: Mapped[TenantDatabase] = relationship(back_populates="credential")


class DemoInstance(Base):
    __tablename__ = "demo_instances"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("systems.id"), nullable=False
    )
    system_access_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("system_access.id"), unique=True, nullable=False
    )
    tenant_database_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant_databases.id")
    )
    status: Mapped[ProvisioningStatus] = mapped_column(
        Enum(ProvisioningStatus, name="demo_instance_status"),
        nullable=False,
        default=ProvisioningStatus.REQUESTED,
    )
    reset_policy: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Avisos por email ya enviados (cada uno una sola vez). Se limpian al
    # renovar la demo, para que el nuevo vencimiento vuelva a avisar.
    reminder_3d_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_1d_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantHostname(Base):
    """Hostname publico (subdominio de nexatecpy.com) que resuelve a un
    tenant+sistema+entorno exacto. Es el UNICO mecanismo de resolucion de
    tenant por Host header -- ver app/services/hostname_resolution.py.
    Nunca se infiere el tenant de un parametro de query/body controlable
    por el navegador. `hostname` ya viene normalizado (lowercase, sin
    puerto) por el servicio antes de insertarse aca."""

    __tablename__ = "tenant_hostnames"
    __table_args__ = (
        UniqueConstraint("hostname", name="uq_tenant_hostnames_hostname"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("systems.id"), nullable=False, index=True
    )
    environment: Mapped[Environment] = mapped_column(
        Enum(Environment, name="tenant_hostname_environment"), nullable=False
    )
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProductionInstance(Base):
    __tablename__ = "production_instances"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("systems.id"), nullable=False
    )
    system_access_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("system_access.id"), unique=True, nullable=False
    )
    tenant_database_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant_databases.id")
    )
    status: Mapped[ProvisioningStatus] = mapped_column(
        Enum(ProvisioningStatus, name="production_instance_status"),
        nullable=False,
        default=ProvisioningStatus.REQUESTED,
    )
    version: Mapped[str | None] = mapped_column(String(40))
    health_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
