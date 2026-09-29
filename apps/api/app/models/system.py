import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ImplementationStatus(str, enum.Enum):
    """Distingue la DEFINICION de un producto (este registro, sus modulos,
    su schema de configuracion) de si el SOFTWARE detras ya existe. Un
    Product Template PENDING es completamente valido -- describe que
    vamos a ofrecer, no finge que ya esta funcionando."""

    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    READY = "READY"


class System(Base):
    """Product Template. Catalogo de soluciones NEXATEC (ERP, CRM, BI,
    etc.) Y su definicion no-code: modulos disponibles, schema de
    configuracion, limites default. El admin gestiona todo esto sin tocar
    codigo (Product Studio); `config_schema`/`modules_schema` son datos
    (listas de campos), nunca codigo ejecutable -- ver
    app/services/config_schema.py para la validacion que lo garantiza."""

    __tablename__ = "systems"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    short_description: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    demo_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    production_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Controla aparicion automatica en el catalogo publico (/ , /soluciones)
    # sin tocar frontend -- ver app/routers/systems.py::list_public_systems.
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    implementation_status: Mapped[ImplementationStatus] = mapped_column(
        Enum(ImplementationStatus, name="system_implementation_status"),
        nullable=False, default=ImplementationStatus.PENDING,
    )

    icon: Mapped[str | None] = mapped_column(String(50))
    image_url: Mapped[str | None] = mapped_column(String(500))
    video_url: Mapped[str | None] = mapped_column(String(500))

    default_demo_duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    default_max_users: Mapped[int | None] = mapped_column(Integer)
    default_storage_mb: Mapped[int | None] = mapped_column(Integer)

    # Listas de definiciones de campo, ver app/services/config_schema.py
    # para la forma exacta y la validacion (nunca codigo ejecutable).
    config_schema: Mapped[list | None] = mapped_column(JSONB)
    modules_schema: Mapped[list | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
