"""Seed SOLO para desarrollo: carga el catalogo de sistemas con las
soluciones reales que NEXATEC ya publica en su sitio (ERP, CRM, BI,
Automatizacion). No crea usuarios ni passwords por defecto.

Uso: python -m app.seed_dev
"""

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.system import System

_SYSTEMS = [
    dict(
        slug="erp",
        name="ERP",
        short_description="Ventas, caja, stock, clientes y control operativo diario.",
        category="Gestion",
        demo_available=True,
        production_available=True,
        sort_order=1,
    ),
    dict(
        slug="crm",
        name="CRM",
        short_description="Seguimiento de leads, oportunidades, agenda y atencion al cliente.",
        category="Ventas",
        demo_available=True,
        production_available=True,
        sort_order=2,
    ),
    dict(
        slug="business-intelligence",
        name="Business Intelligence",
        short_description="KPIs, tableros ejecutivos y analisis de rentabilidad.",
        category="Analitica",
        demo_available=True,
        production_available=True,
        sort_order=3,
    ),
    dict(
        slug="automatizacion",
        name="Automatizacion",
        short_description="Procesos automaticos, alertas y reportes programados.",
        category="Operaciones",
        demo_available=False,
        production_available=True,
        sort_order=4,
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
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run()
