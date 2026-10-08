"""Datos de la empresa demo ficticia. SOLO para bases de entorno DEMO.

Reglas:
- Todo es inventado: nombres de fantasia evidentes, ningun dato de clientes
  reales (ni de ALAR ni de nadie).
- RUCs en el rango 999xxxxx con DV valido pero marcados FICTITIOUS (las
  personas juridicas reales empiezan en 80.000.000). La empresa queda con
  `ruc_is_fictitious = True`, y la API impide cargarle un RUC real despues.
- Determinista (UUIDs por uuid5) e idempotente: si la empresa ya existe, no
  se toca nada. Todo en una sola transaccion: o queda completo o no queda nada.
"""

import uuid
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.services.ruc import compute_dv
from app.tenant_models.core import (
    Branch,
    Company,
    Party,
    Product,
    ProductCategory,
    ProductType,
    RucVerificationStatus,
    UnitOfMeasure,
    Warehouse,
)

_NS = uuid.UUID("6f1d3c52-6a1e-4c41-9a43-6e7b5c1d0a01")

DEMO_COMPANY_NAME = "NEXATEC Empresa Demo S.A. — SIMULACIÓN"


def _id(key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, key)


def _fake_ruc(n: int) -> tuple[str, str]:
    base = f"999{n:05d}"
    return base, str(compute_dv(base))


_CATEGORIES = ["Bebidas", "Almacen", "Limpieza", "Servicios"]

# (sku, nombre, tipo, categoria, unidad, impuesto, precio PYG, costo PYG)
_PRODUCTS = [
    ("BEB-AGUA-500", "Agua mineral 500 ml", ProductType.GOOD, "Bebidas", "UN", "IVA10", 5000, 2800),
    ("BEB-GASEOSA-2L", "Gaseosa cola 2 L", ProductType.GOOD, "Bebidas", "UN", "IVA10", 15000, 9500),
    ("BEB-JUGO-1L", "Jugo de naranja 1 L", ProductType.GOOD, "Bebidas", "UN", "IVA10", 12000, 7200),
    ("ALM-ARROZ-1KG", "Arroz tipo 1, 1 kg", ProductType.GOOD, "Almacen", "UN", "IVA5", 9000, 6300),
    ("ALM-AZUCAR-1KG", "Azucar blanca 1 kg", ProductType.GOOD, "Almacen", "UN", "IVA5", 7500, 5100),
    ("ALM-ACEITE-900", "Aceite de girasol 900 ml", ProductType.GOOD, "Almacen", "UN", "IVA5", 16000, 11800),
    ("ALM-YERBA-500", "Yerba mate 500 g", ProductType.GOOD, "Almacen", "UN", "IVA5", 14000, 9800),
    ("LIM-LAVANDINA-1L", "Lavandina 1 L", ProductType.GOOD, "Limpieza", "UN", "IVA10", 6000, 3600),
    ("LIM-DETERGENTE-750", "Detergente 750 ml", ProductType.GOOD, "Limpieza", "UN", "IVA10", 11000, 6900),
    ("LIM-JABON-POLVO-1KG", "Jabon en polvo 1 kg", ProductType.GOOD, "Limpieza", "UN", "IVA10", 22000, 15400),
    ("SRV-ENTREGA", "Servicio de entrega a domicilio", ProductType.SERVICE, "Servicios", "SRV", "IVA10", 20000, 0),
    ("SRV-INSTALACION", "Instalacion y puesta en marcha", ProductType.SERVICE, "Servicios", "HR", "IVA10", 150000, 0),
]

# (clave, razon social, cliente, proveedor, plazo dias, limite credito PYG)
_PARTIES = [
    ("cli-1", "Comercial Ficticia Uno S.A.", True, False, 30, 5_000_000),
    ("cli-2", "Almacen Imaginario Dos S.R.L.", True, False, 15, 2_000_000),
    ("cli-3", "Supermercado Inventado Tres S.A.", True, False, 30, 15_000_000),
    ("cli-4", "Despensa de Prueba Cuatro", True, False, 0, 0),
    ("cli-5", "Restaurante Simulado Cinco S.R.L.", True, False, 7, 1_500_000),
    ("cli-6", "Consumidor Final (demo)", True, False, 0, 0),
    ("prov-1", "Distribuidora Ficticia del Este S.A.", False, True, 30, 0),
    ("prov-2", "Importadora Inventada S.R.L.", False, True, 45, 0),
    ("prov-3", "Embotelladora de Prueba S.A.", False, True, 30, 0),
    ("mix-1", "Mayorista Simulado Mixto S.A.", True, True, 30, 8_000_000),
]


def seed_demo_company(engine: Engine) -> bool:
    """Carga la empresa demo si la base esta vacia. Devuelve True si cargo,
    False si ya estaba (idempotente)."""
    with Session(bind=engine) as db, db.begin():
        if db.get(Company, 1) is not None:
            return False

        company_ruc, company_dv = _fake_ruc(1)
        db.add(Company(
            id=1, legal_name=DEMO_COMPANY_NAME, trade_name="Empresa Demo",
            ruc=company_ruc, ruc_dv=company_dv, ruc_is_fictitious=True,
            address="Direccion ficticia 123, Asuncion (demo)", phone="(000) 000-000",
            email="demo@ejemplo.invalid", base_currency="PYG", timezone="America/Asuncion",
        ))

        branch = Branch(id=_id("branch-cen"), code="CEN", name="Casa central (demo)", establishment_code="001")
        db.add(branch)
        db.flush()
        db.add_all([
            Warehouse(id=_id("wh-principal"), code="DEP-01", name="Deposito principal", branch_id=branch.id),
            Warehouse(id=_id("wh-salon"), code="DEP-02", name="Salon de ventas", branch_id=branch.id),
        ])

        categories = {name: ProductCategory(id=_id(f"cat-{name}"), name=name) for name in _CATEGORIES}
        db.add_all(categories.values())
        units = {u.code: u.id for u in db.execute(select(UnitOfMeasure)).scalars()}
        db.flush()

        for sku, name, ptype, cat, unit, tax, price, cost in _PRODUCTS:
            db.add(Product(
                id=_id(f"prod-{sku}"), sku=sku, name=name, product_type=ptype,
                category_id=categories[cat].id, unit_id=units[unit], tax_code=tax,
                sale_price=Decimal(price), average_cost=Decimal(cost),
                tracks_stock=ptype == ProductType.GOOD,
            ))

        for i, (key, name, is_cust, is_supp, terms, limit) in enumerate(_PARTIES, start=2):
            base, dv = _fake_ruc(i)
            db.add(Party(
                id=_id(f"party-{key}"), legal_name=name, ruc=base, ruc_dv=dv,
                ruc_status=RucVerificationStatus.FICTITIOUS, is_customer=is_cust, is_supplier=is_supp,
                email=f"{key}@ejemplo.invalid", payment_terms_days=terms, credit_limit=Decimal(limit),
            ))
    return True
