import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.media import MediaAsset
from app.models.tenancy_enums import Environment
from app.security.session_auth import CurrentUser, get_current_user
from app.security.tenancy_rbac import assert_tenant_membership
from app.services.file_validation import IMAGE_MIME_TYPES, detect_type_from_content
from app.services.image_processing import generate_thumbnail, strip_exif_and_get_dimensions
from app.services.storage import build_object_key, delete_object, generate_presigned_get_url, upload_object

router = APIRouter(prefix="/api/media", tags=["media"])

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB, configurable a futuro por plan/tenant


class MediaAssetOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    url: str
    thumbnail_url: str | None

    model_config = ConfigDict(from_attributes=True)


def _get_owned_asset_or_404(db: Session, current_user: CurrentUser, asset_id: uuid.UUID) -> MediaAsset:
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    # Nunca confiar en que el id "parece" del tenant correcto: se verifica
    # membership real, igual que con cualquier otro recurso tenant-scoped.
    assert_tenant_membership(db, current_user, asset.tenant_id)
    return asset


@router.post("/upload", response_model=MediaAssetOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    tenant_id: uuid.UUID,
    environment: Environment,
    file: UploadFile,
    system_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    # IDOR: un CLIENT_USER no puede subir "a nombre de" otro tenant solo
    # porque mando un tenant_id distinto en el form.
    assert_tenant_membership(db, current_user, tenant_id)

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Archivo demasiado grande (maximo {MAX_UPLOAD_SIZE_BYTES // 1024 // 1024} MB).",
        )

    # Nunca se confia en la extension del nombre de archivo ni en el
    # Content-Type declarado por el cliente -- se detecta por contenido.
    detected = detect_type_from_content(content)
    if detected is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tipo de archivo no soportado o contenido invalido.",
        )

    sha256_hash = hashlib.sha256(content).hexdigest()
    width = height = None
    thumbnail_key = None

    if detected.mime_type in IMAGE_MIME_TYPES:
        try:
            content, width, height = strip_exif_and_get_dimensions(content)
            thumbnail_bytes = generate_thumbnail(content)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No se pudo procesar la imagen."
            )

    storage_key = build_object_key(tenant_id, environment, system_id, detected.extension)
    upload_object(storage_key, content, detected.mime_type)

    if detected.mime_type in IMAGE_MIME_TYPES:
        thumbnail_key = storage_key.rsplit(".", 1)[0] + "-thumb.jpg"
        upload_object(thumbnail_key, thumbnail_bytes, "image/jpeg")

    asset = MediaAsset(
        tenant_id=tenant_id,
        system_id=system_id,
        environment=environment,
        owner_user_id=current_user.id,
        original_filename=file.filename or "sin-nombre",
        storage_key=storage_key,
        thumbnail_key=thumbnail_key,
        mime_type=detected.mime_type,
        size_bytes=len(content),
        sha256_hash=sha256_hash,
        width=width,
        height=height,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    return MediaAssetOut(
        id=asset.id, tenant_id=asset.tenant_id, original_filename=asset.original_filename,
        mime_type=asset.mime_type, size_bytes=asset.size_bytes, width=asset.width, height=asset.height,
        url=generate_presigned_get_url(asset.storage_key),
        thumbnail_url=generate_presigned_get_url(asset.thumbnail_key) if asset.thumbnail_key else None,
    )


@router.get("/{asset_id}", response_model=MediaAssetOut)
def get_media(
    asset_id: uuid.UUID, db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)
):
    asset = _get_owned_asset_or_404(db, current_user, asset_id)
    return MediaAssetOut(
        id=asset.id, tenant_id=asset.tenant_id, original_filename=asset.original_filename,
        mime_type=asset.mime_type, size_bytes=asset.size_bytes, width=asset.width, height=asset.height,
        url=generate_presigned_get_url(asset.storage_key),
        thumbnail_url=generate_presigned_get_url(asset.thumbnail_key) if asset.thumbnail_key else None,
    )


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_media(
    asset_id: uuid.UUID, db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)
):
    asset = _get_owned_asset_or_404(db, current_user, asset_id)
    delete_object(asset.storage_key)
    if asset.thumbnail_key:
        delete_object(asset.thumbnail_key)
    db.delete(asset)
    db.commit()
