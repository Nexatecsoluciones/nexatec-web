"""CMS del sitio: borrador vs publicado, validacion (solo texto, sin campos
extra), historial inmutable, restauracion y permisos por rol."""

import copy
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import User, UserSession
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.site_content import DEFAULTS

PASSWORD = "ClaveDePruebaSegura123"


@pytest.fixture(scope="module")
def clients():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    users, out = [], {}
    for role in (Role.SUPER_ADMIN, Role.ADMIN, Role.SUPPORT):
        u = User(email=f"site-{role.value.lower()}-{sfx}@example.com", password_hash=hash_password(PASSWORD), role=role)
        db.add(u)
        db.commit()
        users.append(u)
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": u.email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
        out[role] = c
    out["anon"] = TestClient(app)
    # Estado conocido: borrador = publicado.
    out[Role.ADMIN].post("/api/admin/site/home/discard")
    yield out
    out[Role.ADMIN].post("/api/admin/site/home/discard")
    uids = [u.id for u in users]
    db.query(UserSession).filter(UserSession.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
    db.commit()
    db.close()


def _draft(c):
    return c.get("/api/admin/site/home").json()


def test_public_returns_valid_content_without_login(clients):
    r = clients["anon"].get("/api/public/site/home")
    assert r.status_code == 200 and r.json()["hero"]["title"]
    assert "max-age" in r.headers["cache-control"]
    assert clients["anon"].get("/api/public/site/otra").status_code == 404


def test_draft_is_not_public_until_published(clients):
    c = clients[Role.ADMIN]
    page = _draft(c)
    content = copy.deepcopy(page["draft"])
    marker = f"Titulo de prueba {uuid.uuid4().hex[:6]}"
    content["hero"]["title"] = marker
    r = c.put("/api/admin/site/home/draft", json={"content": content})
    assert r.status_code == 200 and r.json()["has_unpublished_changes"]
    assert clients["anon"].get("/api/public/site/home").json()["hero"]["title"] != marker

    pub = c.post("/api/admin/site/home/publish").json()
    assert pub["published_version"] == page["published_version"] + 1 and not pub["has_unpublished_changes"]
    assert clients["anon"].get("/api/public/site/home").json()["hero"]["title"] == marker
    versions = c.get("/api/admin/site/home/versions").json()
    assert versions[0]["version"] == pub["published_version"]

    # Restaurar la version anterior va al BORRADOR, no al publico.
    prev = page["published_version"]
    r = c.post(f"/api/admin/site/home/versions/{prev}/restore").json()
    assert r["has_unpublished_changes"] and r["draft"]["hero"]["title"] == page["published"]["hero"]["title"]
    assert clients["anon"].get("/api/public/site/home").json()["hero"]["title"] == marker
    c.post("/api/admin/site/home/publish")
    assert clients["anon"].get("/api/public/site/home").json()["hero"]["title"] == page["published"]["hero"]["title"]


@pytest.mark.parametrize("mutate,why", [
    (lambda d: d.update(extra="x"), "campo desconocido"),
    (lambda d: d["hero"].update(title=""), "titulo vacio"),
    (lambda d: d["hero"].update(title="x" * 121), "titulo largo"),
    (lambda d: d["hero"].update(cta_url="javascript:alert(1)"), "url editable"),
    (lambda d: d.update(plans=[]), "sin planes"),
    (lambda d: d["plans"][0].update(items=["ok"] * 9), "demasiados items"),
    (lambda d: d["faqs"].append({"question": "q", "answer": "a\x00b"}), "caracter de control"),
    (lambda d: d.update(sectors=[""]), "rubro vacio"),
])
def test_validation_rejects(clients, mutate, why):
    content = copy.deepcopy(DEFAULTS["home"])
    mutate(content)
    r = clients[Role.ADMIN].put("/api/admin/site/home/draft", json={"content": content})
    assert r.status_code == 422, why


def test_html_is_stored_as_plain_text(clients):
    """No se filtra: se guarda tal cual y React lo escapa. Lo importante es
    que no exista un campo que se renderice como HTML o como href."""
    c = clients[Role.ADMIN]
    content = copy.deepcopy(_draft(c)["draft"])
    content["announcement"] = "<b>Promo</b>"
    assert c.put("/api/admin/site/home/draft", json={"content": content}).json()["draft"]["announcement"] == "<b>Promo</b>"
    c.post("/api/admin/site/home/discard")


def test_roles(clients):
    content = copy.deepcopy(DEFAULTS["home"])
    support = clients[Role.SUPPORT]
    assert support.get("/api/admin/site/home").json()["can_edit"] is False
    assert support.put("/api/admin/site/home/draft", json={"content": content}).status_code == 403
    assert support.post("/api/admin/site/home/publish").status_code == 403
    assert clients["anon"].get("/api/admin/site/home").status_code == 401
    assert clients[Role.SUPER_ADMIN].get("/api/admin/site/home").json()["can_edit"] is True


def test_published_history_is_immutable():
    db = SessionLocal()
    try:
        with pytest.raises(Exception, match="inmutable"):
            db.execute(text("UPDATE site_page_versions SET version = version WHERE slug = 'home'"))
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_unpublished_is_sql_null():
    db = SessionLocal()
    try:
        assert db.execute(text("SELECT count(*) FROM site_pages WHERE jsonb_typeof(published) = 'null'")).scalar() == 0
    finally:
        db.close()
