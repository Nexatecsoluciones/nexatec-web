"""Seed SOLO para desarrollo: carga el catalogo de sistemas con las
soluciones reales que NEXATEC ya publica en su sitio (ERP, CRM, BI,
Automatizacion), marcadas is_public para que aparezcan en el catalogo
publico ( / y /soluciones ) sin tocar frontend. No crea usuarios ni
passwords por defecto -- ver app/cli.py para el SUPER_ADMIN.

Uso: python -m app.seed_dev
"""

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.system import System

# CRM lleva un config_schema/modules_schema de ejemplo real (no ficticio:
# son las opciones que un CRM basico necesita) para demostrar el motor
# no-code del Product Studio. El resto arranca sin schema -- se define
# desde /admin/productos cuando corresponda, no hace falta inventarlo aca.
_CRM_CONFIG_SCHEMA = [
    {"key": "moneda", "label": "Moneda", "type": "select", "required": True, "options": ["PYG", "USD"], "default": "PYG"},
    {"key": "zona_horaria", "label": "Zona horaria", "type": "text", "required": False, "default": "America/Asuncion"},
    {"key": "webhook_url", "label": "Webhook de notificaciones", "type": "text", "required": False},
]
_CRM_MODULES_SCHEMA = [
    {"key": "leads", "label": "Leads", "default_enabled": True},
    {"key": "pipeline", "label": "Pipeline de oportunidades", "default_enabled": True},
    {"key": "campanas", "label": "Campañas de email", "default_enabled": False},
    {"key": "reportes", "label": "Reportes avanzados", "default_enabled": False},
]

_SYSTEMS = [
    dict(
        slug="erp",
        name="ERP",
        short_description="Ventas, caja, stock, clientes y control operativo diario.",
        category="Gestion",
        demo_available=True,
        production_available=True,
        sort_order=1,
        is_public=True,
    ),
    dict(
        slug="crm",
        name="CRM",
        short_description="Seguimiento de leads, oportunidades, agenda y atencion al cliente.",
        category="Ventas",
        demo_available=True,
        production_available=True,
        sort_order=2,
        is_public=True,
        config_schema=_CRM_CONFIG_SCHEMA,
        modules_schema=_CRM_MODULES_SCHEMA,
    ),
    dict(
        slug="business-intelligence",
        name="Business Intelligence",
        short_description="KPIs, tableros ejecutivos y analisis de rentabilidad.",
        category="Analitica",
        demo_available=True,
        production_available=True,
        sort_order=3,
        is_public=True,
    ),
    dict(
        slug="automatizacion",
        name="Automatizacion",
        short_description="Procesos automaticos, alertas y reportes programados.",
        category="Operaciones",
        demo_available=False,
        production_available=True,
        sort_order=4,
        is_public=True,
    ),
]


def run() -> None:
    settings = get_settings()
    if settings.is_production:
        raise SystemExit("seed_dev no debe ejecutarse en produccion.")

    db = SessionLocal()
    try:
        for data in _SYSTEMS:
            existing = db.query(System).filter(System.slug == data["slug"]).first()
            if existing is None:
                db.add(System(**data))
            else:
                # Idempotente: si ya existe (de una corrida anterior de
                # FASE 3), actualiza los campos nuevos sin duplicar.
                for key, value in data.items():
                    setattr(existing, key, value)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run()
