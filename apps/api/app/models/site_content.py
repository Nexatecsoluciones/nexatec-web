"""Contenido editable del sitio publico (CMS sin codigo).

Una fila por pagina (`slug`): `draft` es lo que se edita en el Control
Center, `published` lo que ven los visitantes. Publicar copia el borrador a
`published` y deja una fila inmutable en `site_page_versions` (historial y
restauracion). El contenido es JSON validado por app/services/site_content.py:
solo texto plano, sin HTML ni URLs editables."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SitePage(Base):
    __tablename__ = "site_pages"

    slug: Mapped[str] = mapped_column(String(40), primary_key=True)
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    published_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draft_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    draft_updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class SitePageVersion(Base):
    __tablename__ = "site_page_versions"
    __table_args__ = (UniqueConstraint("slug", "version", name="uq_site_page_versions_slug_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(40), ForeignKey("site_pages.slug"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Sin FK a users a proposito: el historial es inmutable y no puede
    # impedir que se borre un usuario (mismo criterio que audit_logs).
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
