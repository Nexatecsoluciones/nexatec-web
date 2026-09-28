"""Procesamiento de imagenes: thumbnail + remocion de metadata EXIF (puede
traer geolocalizacion u otros datos sensibles del dispositivo de origen).
Video/FFmpeg queda preparado pero no implementado (ver
docs/ARCHITECTURE.md) -- no hay flujo de video todavia que lo necesite."""

import io

from PIL import Image

THUMBNAIL_MAX_SIZE = (400, 400)


def strip_exif_and_get_dimensions(content: bytes) -> tuple[bytes, int, int]:
    """Reescribe la imagen sin metadata EXIF (Pillow no copia el bloque
    EXIF salvo que se pase explicitamente al guardar) y devuelve sus
    dimensiones reales validadas por Pillow (no por el header declarado)."""
    with Image.open(io.BytesIO(content)) as img:
        width, height = img.size
        img = img.convert("RGB") if img.mode in ("P", "CMYK") else img
        buffer = io.BytesIO()
        save_format = img.format or "JPEG"
        img.save(buffer, format=save_format)
        return buffer.getvalue(), width, height


def generate_thumbnail(content: bytes) -> bytes:
    with Image.open(io.BytesIO(content)) as img:
        img = img.convert("RGB") if img.mode in ("P", "CMYK", "RGBA") else img
        img.thumbnail(THUMBNAIL_MAX_SIZE)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=82)
        return buffer.getvalue()
