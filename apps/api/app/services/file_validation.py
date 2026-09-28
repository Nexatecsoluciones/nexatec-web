"""Validacion de archivos por contenido real (magic bytes), nunca por
extension ni por el Content-Type que declara el cliente -- ambos son
triviales de falsificar. No se usa `python-magic` (requiere libmagic a
nivel de sistema operativo, otra dependencia mas para instalar en el
servidor) porque el conjunto de tipos soportado hoy es chico y las firmas
son simples de verificar a mano; si el catalogo de tipos crece mucho,
migrar a python-magic es la mejora natural.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedType:
    mime_type: str
    extension: str


def _is_jpeg(head: bytes) -> bool:
    return head[:3] == b"\xff\xd8\xff"


def _is_png(head: bytes) -> bool:
    return head[:8] == b"\x89PNG\r\n\x1a\n"


def _is_webp(head: bytes) -> bool:
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _is_gif(head: bytes) -> bool:
    return head[:6] in (b"GIF87a", b"GIF89a")


def _is_pdf(head: bytes) -> bool:
    return head[:5] == b"%PDF-"


_DETECTORS: list[tuple[Callable[[bytes], bool], DetectedType]] = [
    (_is_jpeg, DetectedType("image/jpeg", "jpg")),
    (_is_png, DetectedType("image/png", "png")),
    (_is_webp, DetectedType("image/webp", "webp")),
    (_is_gif, DetectedType("image/gif", "gif")),
    (_is_pdf, DetectedType("application/pdf", "pdf")),
]

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def detect_type_from_content(content: bytes) -> DetectedType | None:
    """Devuelve el tipo real segun los primeros bytes del archivo, o None
    si no coincide con ningun formato soportado -- independientemente de
    que extension o Content-Type haya declarado el cliente."""
    head = content[:16]
    for check, detected in _DETECTORS:
        if check(head):
            return detected
    return None
