"""CMS del sitio publico: esquema, contenido por defecto y flujo
borrador -> publicar -> historial.

Seguridad: todo campo es texto plano con largo maximo (React lo escapa al
renderizar) y NO hay URLs editables -- los botones van siempre a /demo o a
WhatsApp con un mensaje editable. Asi una cuenta de admin comprometida no
puede inyectar scripts ni links de phishing en el sitio."""

import copy
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.site_content import SitePage, SitePageVersion

PAGES = ("home",)


def _no_control_chars(v: str) -> str:
    v = v.strip()
    if any(ord(c) < 32 and c not in "\n\t" for c in v):
        raise ValueError("Texto con caracteres invalidos.")
    return v


class _Text(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, v):
        if isinstance(v, str):
            return _no_control_chars(v)
        if isinstance(v, list):
            return [_no_control_chars(x) if isinstance(x, str) else x for x in v]
        return v


def T(max_length: int, min_length: int = 1):
    return Field(min_length=min_length, max_length=max_length)


class Hero(_Text):
    eyebrow: str = T(60, 0)
    title: str = T(120)
    subtitle: str = T(300, 0)
    note: str = T(120, 0)
    whatsapp_message: str = T(300)


class Card(_Text):
    title: str = T(60)
    text: str = T(300)


class Plan(_Text):
    tag: str = T(40, 0)
    name: str = T(60)
    price: str = T(40)
    sub: str = T(80, 0)
    text: str = T(300, 0)
    items: list[str] = Field(default_factory=list, max_length=8)
    whatsapp_message: str = T(300)
    featured: bool = False

    @field_validator("items")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        if any(not x or len(x) > 80 for x in v):
            raise ValueError("Cada item del plan: 1 a 80 caracteres.")
        return v


class Faq(_Text):
    question: str = T(160)
    answer: str = T(1000)


class Cta(_Text):
    title: str = T(80)
    subtitle: str = T(200, 0)
    whatsapp_message: str = T(300)


class HomeContent(_Text):
    announcement: str = T(160, 0)
    hero: Hero
    services_title: str = T(60)
    services_subtitle: str = T(300, 0)
    services: list[Card] = Field(min_length=1, max_length=8)
    modules_title: str = T(60)
    modules: list[Card] = Field(max_length=12)
    plans_title: str = T(60)
    plans_subtitle: str = T(300, 0)
    plans: list[Plan] = Field(min_length=1, max_length=4)
    sectors_title: str = T(60)
    sectors: list[str] = Field(max_length=20)
    about_title: str = T(60)
    about: str = T(1500, 0)
    faq_title: str = T(60)
    faqs: list[Faq] = Field(max_length=20)
    cta: Cta

    @field_validator("sectors")
    @classmethod
    def _sectors(cls, v: list[str]) -> list[str]:
        if any(not x or len(x) > 60 for x in v):
            raise ValueError("Cada rubro: 1 a 60 caracteres.")
        return v


SCHEMAS = {"home": HomeContent}

# Contenido actual del sitio (antes estaba fijo en apps/web/src/app/page.tsx).
DEFAULTS: dict[str, dict] = {
    "home": {
        "announcement": "",
        "hero": {
            "eyebrow": "Hola, somos NEXATEC",
            "title": "Transformamos tu PyME con tecnologia a medida",
            "subtitle": "Ventas, stock, compras, cobranzas y contabilidad en un solo sistema, pensado para la operacion real de un negocio paraguayo.",
            "note": "14 dias gratis · sin tarjeta · empresa de ejemplo precargada",
            "whatsapp_message": "Hola NEXATEC, quiero agendar una consultoria gratuita para mi negocio.",
        },
        "services_title": "Soluciones",
        "services_subtitle": "Para comercios, talleres, consultorios, centros de servicios y empresas que necesitan orden, control y crecer.",
        "services": [
            {"title": "ERP", "text": "Ventas, caja, stock, compras, cobranzas, pagos y contabilidad conectados: cada operacion genera su asiento."},
            {"title": "CRM", "text": "Seguimiento de clientes, oportunidades, agenda y atencion para vender mas y no perder contactos."},
            {"title": "Business Intelligence", "text": "Tableros con ventas, margen, cartera, stock y caja para decidir con datos reales."},
            {"title": "Automatizacion", "text": "Alertas, recordatorios, reportes programados e integraciones para dejar de hacer tareas a mano."},
        ],
        "modules_title": "Que resuelve el ERP",
        "modules": [
            {"title": "Ventas", "text": "Pedidos con reserva de stock, entrega y comprobante. Precios IVA incluido, descuentos y credito con limite por cliente."},
            {"title": "Inventario", "text": "Varios depositos, kardex inmutable, costo promedio ponderado y transferencias. Nunca stock negativo."},
            {"title": "Compras", "text": "Ordenes de compra, recepciones parciales y control de la factura del proveedor contra lo recibido."},
            {"title": "Cobranzas y pagos", "text": "Cobros, anticipos, antiguedad de saldos y extracto por cliente y proveedor."},
            {"title": "Contabilidad", "text": "Asientos automaticos, balance de comprobacion, estado de resultados y balance general."},
            {"title": "CRM", "text": "Prospectos, oportunidades por etapa, agenda de seguimiento y pronostico de ventas."},
            {"title": "Seguridad", "text": "Base de datos separada por empresa, roles por funcion, auditoria y backups diarios cifrados."},
        ],
        "plans_title": "Planes",
        "plans_subtitle": "Elegi como queres empezar. Precios de referencia; te confirmamos la propuesta segun tu negocio.",
        "plans": [
            {"tag": "Mas elegido por PyMEs", "name": "NEXATEC Cloud", "price": "₲ 20.000 / dia", "sub": "Plan mensual desde ₲ 550.000",
             "text": "Sin comprar servidor. Accedes desde cualquier lugar, con backups, soporte mensual y actualizaciones.",
             "items": ["ERP/CRM en la nube", "Acceso remoto", "Backups automaticos", "Soporte mensual incluido"],
             "whatsapp_message": "Hola NEXATEC, quiero solicitar el plan NEXATEC Cloud (desde Gs. 550.000 al mes). ¿Podemos coordinar?",
             "featured": True},
            {"tag": "Pago unico local", "name": "NexaBox Local", "price": "Desde ₲ 9.900.000", "sub": "Sin mensualidad obligatoria",
             "text": "Tu sistema funcionando dentro de tu comercio, con servidor local propio.",
             "items": ["Servidor local instalado", "ERP/CRM configurado", "Backups locales", "Capacitacion inicial"],
             "whatsapp_message": "Hola NEXATEC, quiero informacion del plan NexaBox Local de pago unico.", "featured": False},
            {"tag": "Exclusivo", "name": "NEXATEC Enterprise", "price": "A medida", "sub": "Proyecto segun necesidad",
             "text": "Para empresas que necesitan procesos propios, integraciones y BI avanzado.",
             "items": ["ERP + CRM + BI a medida", "Integraciones", "Infraestructura dedicada", "Acuerdo de confidencialidad"],
             "whatsapp_message": "Hola NEXATEC, quiero agendar una reunion por una solucion Enterprise a medida.", "featured": False},
        ],
        "sectors_title": "Para que tipo de negocio",
        "sectors": ["Comercios y almacenes", "Talleres", "Consultorios", "Barberias y centros de belleza", "Distribuidoras",
                    "Empresas de servicios"],
        "about_title": "Quienes somos",
        "about": "En NEXATEC ayudamos a transformar procesos manuales en sistemas simples, medibles y escalables. Combinamos desarrollo, "
                 "operacion, automatizacion y analisis para que cada cliente pueda vender mejor, controlar mas y crecer con informacion confiable.",
        "faq_title": "Preguntas frecuentes",
        "faqs": [
            {"question": "¿Cuanto dura la demo y que incluye?",
             "answer": "14 dias, gratis. Te habilitamos una empresa de ejemplo con productos, clientes, stock, ventas, compras y contabilidad cargados para que pruebes todo el circuito."},
            {"question": "¿Emite factura electronica (SIFEN)?",
             "answer": "Todavia no. Hoy los comprobantes del sistema son internos de gestion y llevan la leyenda \"sin validez tributaria\". La integracion con SIFEN se habilita recien cuando este homologada; no lo vamos a presentar como hecho antes."},
            {"question": "¿Mis datos quedan mezclados con los de otras empresas?",
             "answer": "No. Cada empresa tiene su propia base de datos, con su propio usuario de acceso. Ademas hay backups diarios cifrados."},
            {"question": "¿Puedo pasar de la demo a usar el sistema en serio?",
             "answer": "Si. Se crea tu sistema de produccion aparte, vacio, con tus datos reales. Los datos ficticios de la demo no se mezclan con los reales."},
            {"question": "¿Como se contrata?",
             "answer": "Por WhatsApp o email: coordinamos una charla, elegis el plan y te dejamos el sistema listo. Hoy no cobramos con tarjeta en linea."},
        ],
        "cta": {"title": "Hablemos de tu negocio", "subtitle": "Te contamos como quedaria el sistema con tus procesos.",
                "whatsapp_message": "Hola NEXATEC, quiero agendar una consultoria gratuita para mi negocio."},
    },
}


class SiteContentError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _check_slug(slug: str) -> None:
    if slug not in PAGES:
        raise SiteContentError("Pagina inexistente.", 404)


def validate(slug: str, content: dict) -> dict:
    _check_slug(slug)
    return SCHEMAS[slug].model_validate(content).model_dump(mode="json")


def _page(db: Session, slug: str, lock: bool = False) -> SitePage:
    """Crea la fila al primer uso con el contenido por defecto como
    borrador y como publicado (version 0 = el sitio tal cual estaba)."""
    _check_slug(slug)
    stmt = select(SitePage).where(SitePage.slug == slug)
    if lock:
        stmt = stmt.with_for_update()
    page = db.execute(stmt).scalar_one_or_none()
    if page is None:
        page = SitePage(slug=slug, draft=copy.deepcopy(DEFAULTS[slug]), published=None, published_version=0)
        db.add(page)
        db.flush()
    return page


def public_content(db: Session, slug: str) -> dict:
    _check_slug(slug)
    page = db.get(SitePage, slug)
    return page.published if page is not None and page.published is not None else DEFAULTS[slug]


def get_page(db: Session, slug: str) -> SitePage:
    return _page(db, slug)


def save_draft(db: Session, slug: str, content: dict, user_id: uuid.UUID) -> SitePage:
    clean = validate(slug, content)
    page = _page(db, slug, lock=True)
    page.draft = clean
    page.draft_updated_at = datetime.now(timezone.utc)
    page.draft_updated_by = user_id
    return page


def publish(db: Session, slug: str, user_id: uuid.UUID) -> SitePage:
    page = _page(db, slug, lock=True)
    clean = validate(slug, page.draft)  # el esquema pudo cambiar desde que se guardo
    now = datetime.now(timezone.utc)
    page.published_version += 1
    page.published = clean
    page.published_at = now
    page.published_by = user_id
    db.add(SitePageVersion(id=uuid.uuid4(), slug=slug, version=page.published_version, content=clean,
                           published_at=now, published_by=user_id))
    return page


def discard_draft(db: Session, slug: str, user_id: uuid.UUID) -> SitePage:
    page = _page(db, slug, lock=True)
    page.draft = copy.deepcopy(page.published if page.published is not None else DEFAULTS[slug])
    page.draft_updated_at = datetime.now(timezone.utc)
    page.draft_updated_by = user_id
    return page


def restore_version(db: Session, slug: str, version: int, user_id: uuid.UUID) -> SitePage:
    """Copia una version publicada al BORRADOR (no publica: hay que revisar
    y publicar de nuevo, y eso deja su propia version en el historial)."""
    page = _page(db, slug, lock=True)
    if version == 0:
        content = DEFAULTS[slug]
    else:
        row = db.execute(select(SitePageVersion).where(SitePageVersion.slug == slug, SitePageVersion.version == version)
                         ).scalar_one_or_none()
        if row is None:
            raise SiteContentError("Version inexistente.", 404)
        content = row.content
    page.draft = copy.deepcopy(content)
    page.draft_updated_at = datetime.now(timezone.utc)
    page.draft_updated_by = user_id
    return page


def history(db: Session, slug: str, limit: int = 50) -> list[SitePageVersion]:
    _check_slug(slug)
    return list(db.execute(select(SitePageVersion).where(SitePageVersion.slug == slug)
                           .order_by(SitePageVersion.version.desc()).limit(limit)).scalars())


def has_unpublished_changes(page: SitePage) -> bool:
    current = page.published if page.published is not None else DEFAULTS[page.slug]
    return page.draft != current

