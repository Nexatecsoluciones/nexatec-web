"""FASE 5: upload real contra Garage (S3), validacion por contenido real
(no por extension/Content-Type declarado), y el mismo test critico de
aislamiento por tenant que el resto del sistema."""

import io
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import Tenant, User, UserSession
from app.models.media import MediaAsset
from app.models.tenancy import TenantUser
from app.models.tenancy_enums import TenantMemberRole, TenantMemberStatus, TenantStatus
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.storage import delete_object

PASSWORD = "ClaveDePruebaSegura123"


def _make_real_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (300, 200), color=(120, 200, 180))
    # EXIF con datos que NO deberian sobrevivir el procesamiento.
    exif = img.getexif()
    exif[271] = "CamaraDePrueba"  # Make
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


@pytest.fixture()
def tenant_with_user():
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(
        slug=f"media-t-{suffix}", legal_name="Media Test SRL", display_name="Media Test",
        status=TenantStatus.ACTIVE,
    )
    db.add(tenant)
    db.flush()
    user = User(
        email=f"media-user-{suffix}@example.com", password_hash=hash_password(PASSWORD),
        role=Role.CLIENT_USER, tenant_id=tenant.id,
    )
    db.add(user)
    db.flush()
    db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE))
    db.commit()

    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD, "turnstile_token": "dev"})
    assert resp.status_code == 200, resp.text

    yield {"tenant": tenant, "user": user, "client": client}

    uploaded = db.query(MediaAsset).filter(MediaAsset.tenant_id == tenant.id).all()
    for a in uploaded:
        delete_object(a.storage_key)
        if a.thumbnail_key:
            delete_object(a.thumbnail_key)
    db.query(MediaAsset).filter(MediaAsset.tenant_id == tenant.id).delete()
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.commit()
    db.close()


def test_upload_real_image_strips_exif_and_generates_thumbnail(tenant_with_user):
    tenant = tenant_with_user["tenant"]
    client = tenant_with_user["client"]
    jpeg_bytes = _make_real_jpeg_bytes()

    resp = client.post(
        "/api/media/upload",
        params={"tenant_id": str(tenant.id), "environment": "DEMO"},
        files={"file": ("foto.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mime_type"] == "image/jpeg"
    assert body["width"] == 300 and body["height"] == 200
    assert body["url"].startswith("http")
    assert body["thumbnail_url"] is not None

    # La URL firmada realmente sirve el archivo, y ya no tiene el EXIF.
    file_resp = httpx.get(body["url"])
    assert file_resp.status_code == 200
    downloaded = Image.open(io.BytesIO(file_resp.content))
    assert 271 not in downloaded.getexif()

    thumb_resp = httpx.get(body["thumbnail_url"])
    assert thumb_resp.status_code == 200
    thumb_img = Image.open(io.BytesIO(thumb_resp.content))
    assert max(thumb_img.size) <= 400


def test_upload_rejects_fake_extension_with_wrong_content(tenant_with_user):
    tenant = tenant_with_user["tenant"]
    client = tenant_with_user["client"]
    fake_image = b"esto no es una imagen, es texto plano" * 10

    resp = client.post(
        "/api/media/upload",
        params={"tenant_id": str(tenant.id), "environment": "DEMO"},
        files={"file": ("malicioso.jpg", fake_image, "image/jpeg")},
    )
    assert resp.status_code == 400


def test_upload_rejects_oversized_file(tenant_with_user):
    tenant = tenant_with_user["tenant"]
    client = tenant_with_user["client"]
    oversized = _make_real_jpeg_bytes() + b"\x00" * (11 * 1024 * 1024)

    resp = client.post(
        "/api/media/upload",
        params={"tenant_id": str(tenant.id), "environment": "DEMO"},
        files={"file": ("grande.jpg", oversized, "image/jpeg")},
    )
    assert resp.status_code == 413


def test_upload_rejects_tenant_not_a_member_of(tenant_with_user):
    other_tenant_id = uuid.uuid4()
    client = tenant_with_user["client"]

    resp = client.post(
        "/api/media/upload",
        params={"tenant_id": str(other_tenant_id), "environment": "DEMO"},
        files={"file": ("foto.jpg", _make_real_jpeg_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 404


def test_idor_tenant_b_cannot_read_or_delete_tenant_a_media(tenant_with_user):
    tenant_a = tenant_with_user["tenant"]
    client_a = tenant_with_user["client"]

    upload = client_a.post(
        "/api/media/upload",
        params={"tenant_id": str(tenant_a.id), "environment": "DEMO"},
        files={"file": ("foto.jpg", _make_real_jpeg_bytes(), "image/jpeg")},
    )
    assert upload.status_code == 201
    asset_id = upload.json()["id"]

    # Tenant B, independiente del fixture.
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    tenant_b = Tenant(slug=f"media-tb-{suffix}", legal_name="B SRL", display_name="B", status=TenantStatus.ACTIVE)
    db.add(tenant_b)
    db.flush()
    user_b = User(email=f"media-b-{suffix}@example.com", password_hash=hash_password(PASSWORD), role=Role.CLIENT_USER, tenant_id=tenant_b.id)
    db.add(user_b)
    db.flush()
    db.add(TenantUser(tenant_id=tenant_b.id, user_id=user_b.id, role=TenantMemberRole.CLIENT_USER, status=TenantMemberStatus.ACTIVE))
    db.commit()

    client_b = TestClient(app)
    login_b = client_b.post("/api/auth/login", json={"email": user_b.email, "password": PASSWORD, "turnstile_token": "dev"})
    assert login_b.status_code == 200

    get_resp = client_b.get(f"/api/media/{asset_id}")
    assert get_resp.status_code == 404

    delete_resp = client_b.delete(f"/api/media/{asset_id}")
    assert delete_resp.status_code == 404

    # Tenant A si puede.
    own_get = client_a.get(f"/api/media/{asset_id}")
    assert own_get.status_code == 200

    db.query(UserSession).filter(UserSession.user_id == user_b.id).delete()
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant_b.id).delete()
    db.query(User).filter(User.id == user_b.id).delete()
    db.query(Tenant).filter(Tenant.id == tenant_b.id).delete()
    db.commit()
    db.close()
