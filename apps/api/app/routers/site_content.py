"""CMS del sitio publico. Lectura publica (solo lo publicado) y edicion en el
Control Center (SUPER_ADMIN / ADMIN; Soporte y Facturacion solo miran)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.db import get_db
from app.security.rbac import require_admin_panel, require_roles
from app.security.roles import Role
from app.security.session_auth import CurrentUser
from app.services import site_content as svc

public_router = APIRouter(prefix="/api/public/site", tags=["public", "site"])
admin_router = APIRouter(prefix="/api/admin/site", tags=["admin", "site"])

_editor = require_roles(Role.SUPER_ADMIN, Role.ADMIN)


class PageOut(BaseModel):
    slug: str
    draft: dict
    published: dict
    published_version: int
    published_at: datetime | None
    draft_updated_at: datetime
    has_unpublished_changes: bool
    can_edit: bool


class DraftIn(BaseModel):
    content: dict


class VersionOut(BaseModel):
    version: int
    published_at: datetime
    published_by: uuid.UUID | None


def _err(exc: svc.SiteContentError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _out(page, user: CurrentUser) -> PageOut:
    return PageOut(
        slug=page.slug, draft=page.draft,
        published=page.published if page.published is not None else svc.DEFAULTS[page.slug],
        published_version=page.published_version, published_at=page.published_at,
        draft_updated_at=page.draft_updated_at, has_unpublished_changes=svc.has_unpublished_changes(page),
        can_edit=user.role in (Role.SUPER_ADMIN, Role.ADMIN),
    )


@public_router.get("/{slug}")
def public_page(slug: str, response: Response, db: Session = Depends(get_db)):
    try:
        content = svc.public_content(db, slug)
    except svc.SiteContentError as exc:
        raise _err(exc)
    # Cache corto en el borde: un cambio publicado se ve en menos de un minuto.
    response.headers["Cache-Control"] = "public, max-age=30"
    return content


@admin_router.get("/{slug}", response_model=PageOut)
def get_page(slug: str, user: CurrentUser = Depends(require_admin_panel()), db: Session = Depends(get_db)):
    try:
        page = svc.get_page(db, slug)
        db.commit()
    except svc.SiteContentError as exc:
        db.rollback()
        raise _err(exc)
    return _out(page, user)


def _mutate(db: Session, user: CurrentUser, action: str, slug: str, fn, meta: dict | None = None) -> PageOut:
    try:
        page = fn()
        db.commit()
    except svc.SiteContentError as exc:
        db.rollback()
        raise _err(exc)
    except ValidationError as exc:
        db.rollback()
        errors = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors(include_url=False, include_input=False)]
        raise HTTPException(status_code=422, detail=errors)
    db.refresh(page)
    log_audit(db, actor_user_id=user.id, tenant_id=None, action=action, resource=f"site_page:{slug}",
              metadata={"published_version": page.published_version, **(meta or {})})
    db.commit()
    return _out(page, user)


@admin_router.put("/{slug}/draft", response_model=PageOut)
def save_draft(slug: str, payload: DraftIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    return _mutate(db, user, "SITE_DRAFT_SAVED", slug, lambda: svc.save_draft(db, slug, payload.content, user.id))


@admin_router.post("/{slug}/publish", response_model=PageOut)
def publish(slug: str, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    return _mutate(db, user, "SITE_PUBLISHED", slug, lambda: svc.publish(db, slug, user.id))


@admin_router.post("/{slug}/discard", response_model=PageOut)
def discard(slug: str, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    return _mutate(db, user, "SITE_DRAFT_DISCARDED", slug, lambda: svc.discard_draft(db, slug, user.id))


@admin_router.post("/{slug}/versions/{version}/restore", response_model=PageOut)
def restore(slug: str, version: int, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    return _mutate(db, user, "SITE_VERSION_RESTORED", slug, lambda: svc.restore_version(db, slug, version, user.id),
                   {"restored_version": version})


@admin_router.get("/{slug}/versions", response_model=list[VersionOut])
def versions(slug: str, user: CurrentUser = Depends(require_admin_panel()), db: Session = Depends(get_db)):
    try:
        return svc.history(db, slug)
    except svc.SiteContentError as exc:
        raise _err(exc)
