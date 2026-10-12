"""Adjuntos de facturas de proveedor (PDF o foto) en Garage.

- El tipo se detecta por el CONTENIDO (no por la extension ni el
  Content-Type declarado): PDF, JPEG, PNG, WebP. Maximo 10 MB, 10 por factura.
- La clave del objeto la genera el servidor, separada por empresa y
  entorno; nunca se usa el nombre de archivo del usuario como ruta.
- Descarga: URL firmada de 5 minutos, emitida despues de verificar que el
  usuario tiene permiso sobre ESA empresa (payables:read).
- Quitar = baja logica con motivo; el objeto se conserva como respaldo.
- El mismo archivo dos veces en la misma factura: 409 (sha256).

Lectura automatica (OCR): etapa posterior."""

import hashlib
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, require
from app.services import storage
from app.services.file_validation import detect_type_from_content
from app.tenant_models.purchases import SupplierInvoice, SupplierInvoiceAttachment

router = APIRouter(prefix="/api/erp/{system_access_id}/supplier-invoices", tags=["erp", "attachments"])

MAX_BYTES = 10 * 1024 * 1024
MAX_PER_INVOICE = 10
ALLOWED = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


class AttachmentOut(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UrlOut(BaseModel):
    url: str
    expires_in: int


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=250)


def _invoice(ctx: ErpContext, invoice_id: uuid.UUID) -> SupplierInvoice:
    inv = ctx.db.get(SupplierInvoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")
    return inv


def _safe_name(name: str | None, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", (name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip(" .")[:150]
    return base or f"factura.{ext}"


@router.get("/{invoice_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(invoice_id: uuid.UUID, ctx: ErpContext = Depends(require("payables:read"))):
    _invoice(ctx, invoice_id)
    return list(ctx.db.execute(select(SupplierInvoiceAttachment).where(
        SupplierInvoiceAttachment.supplier_invoice_id == invoice_id, SupplierInvoiceAttachment.deleted_at.is_(None))
        .order_by(SupplierInvoiceAttachment.created_at)).scalars())


@router.post("/{invoice_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload(invoice_id: uuid.UUID, file: UploadFile = File(...), ctx: ErpContext = Depends(require("payables:write"))):
    _invoice(ctx, invoice_id)
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="El archivo supera 10 MB.")
    if not content:
        raise HTTPException(status_code=422, detail="El archivo esta vacio.")
    detected = detect_type_from_content(content)
    if detected is None or detected.mime_type not in ALLOWED:
        raise HTTPException(status_code=415, detail="Solo PDF o fotos (JPG, PNG, WebP).")
    count = ctx.db.execute(select(func.count()).select_from(SupplierInvoiceAttachment).where(
        SupplierInvoiceAttachment.supplier_invoice_id == invoice_id,
        SupplierInvoiceAttachment.deleted_at.is_(None))).scalar_one()
    if count >= MAX_PER_INVOICE:
        raise HTTPException(status_code=409, detail=f"Maximo {MAX_PER_INVOICE} adjuntos por factura.")
    digest = hashlib.sha256(content).hexdigest()
    key = "supplier-invoices/" + storage.build_object_key(ctx.tenant_id, ctx.access.environment, ctx.access.system_id,
                                                          detected.extension)
    att = SupplierInvoiceAttachment(id=uuid.uuid4(), supplier_invoice_id=invoice_id, object_key=key,
                                    filename=_safe_name(file.filename, detected.extension),
                                    content_type=detected.mime_type, size_bytes=len(content), sha256=digest,
                                    uploaded_by_user_id=ctx.user.id)
    ctx.db.add(att)
    try:
        ctx.db.flush()  # duplicado -> IntegrityError antes de subir nada
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status_code=409, detail="Ese archivo ya esta adjunto a esta factura.")
    storage.upload_object(key, content, detected.mime_type)
    try:
        ctx.db.commit()
    except Exception:
        ctx.db.rollback()
        storage.delete_object(key)  # compensacion: no dejar huerfanos en Garage
        raise
    ctx.db.refresh(att)
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action="ERP_SUPPLIER_INVOICE_ATTACHED",
              resource=f"supplier_invoice:{invoice_id}", metadata={"attachment_id": str(att.id), "bytes": len(content),
                                                                     "type": detected.mime_type})
    ctx.control_db.commit()
    return att


@router.get("/{invoice_id}/attachments/{attachment_id}/url", response_model=UrlOut)
def download_url(invoice_id: uuid.UUID, attachment_id: uuid.UUID, ctx: ErpContext = Depends(require("payables:read"))):
    att = ctx.db.get(SupplierInvoiceAttachment, attachment_id)
    if att is None or att.supplier_invoice_id != invoice_id or att.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Adjunto no encontrado.")
    return UrlOut(url=storage.generate_presigned_get_url(att.object_key), expires_in=storage.PRESIGNED_URL_TTL_SECONDS)


@router.post("/{invoice_id}/attachments/{attachment_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
def remove(invoice_id: uuid.UUID, attachment_id: uuid.UUID, payload: ReasonIn,
           ctx: ErpContext = Depends(require("payables:write"))):
    att = ctx.db.get(SupplierInvoiceAttachment, attachment_id)
    if att is None or att.supplier_invoice_id != invoice_id or att.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Adjunto no encontrado.")
    att.deleted_at = datetime.now(timezone.utc)
    att.deleted_by_user_id = ctx.user.id
    att.delete_reason = payload.reason
    ctx.db.commit()
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action="ERP_SUPPLIER_INVOICE_DETACHED",
              resource=f"supplier_invoice:{invoice_id}", metadata={"attachment_id": str(attachment_id),
                                                                     "reason": payload.reason})
    ctx.control_db.commit()
