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
from app.tenant_models.accounting import Account
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


def _seed_operations(db: Session) -> None:
    """Operaciones de ejemplo hechas con los MISMOS servicios que usa el
    sistema (no inserts directos): stock, facturas, cobros, deuda y asientos
    quedan consistentes por construccion. Fechadas hoy (los servicios no
    permiten registrar en el pasado)."""
    from app.services import accounting, purchases, receivables, sales
    from app.services.receivables import AllocationInput
    from app.tenant_models.accounting import AccountMapping
    from app.tenant_models.receivables import PaymentMethod
    from app.tenant_models.sales import PaymentCondition

    pid = {sku: _id(f"prod-{sku}") for sku, *_ in _PRODUCTS}
    party = {key: _id(f"party-{key}") for key, *_ in _PARTIES}
    wh = _id("wh-principal")

    cash = db.get(AccountMapping, "CASH").account_id
    bank = db.get(AccountMapping, "BANK").account_id
    capital = db.execute(select(Account.id).where(Account.code == "3.1.01")).scalar_one()
    accounting.post_entry(db, accounting.EntryDraft(
        accounting._today(db), "Aporte de capital inicial (demo)", "MANUAL", None,
        [accounting.Line(cash, debit=Decimal(10_000_000)), accounting.Line(bank, debit=Decimal(70_000_000)),
         accounting.Line(capital, credit=Decimal(80_000_000))],
    ))

    def buy(supplier_key, items, receive_ratio, invoice_number, pay_ratio):
        po = purchases.create_po(db, user_id=None, supplier_id=party[supplier_key], warehouse_id=wh, expected_date=None,
                                 notes="Compra de ejemplo (demo)", lines=[
                                     purchases.POLineInput(pid[sku], Decimal(q), Decimal(cost)) for sku, q, cost in items])
        db.flush()
        purchases.confirm_po(db, po.id)
        purchases.receive(db, po.id, None, [
            purchases.ReceiveItem(line.line_no, (line.quantity * Decimal(receive_ratio)).quantize(Decimal("1")))
            for line in po.lines], None)
        if invoice_number:
            b = purchases.Breakdown()
            for line in po.lines:
                share = line.quantity_received / line.quantity
                net = (line.line_net * share).quantize(Decimal("1"))
                vat = (line.line_tax * share).quantize(Decimal("1"))
                if line.tax_rate == 10:
                    b.taxable_10 += net
                    b.vat_10 += vat
                else:
                    b.taxable_5 += net
                    b.vat_5 += vat
            inv = purchases.register_supplier_invoice(
                db, user_id=None, supplier_id=party[supplier_key], supplier_invoice_number=invoice_number,
                supplier_timbrado="00000000", issue_date=receivables.local_today(db), due_date=None, breakdown=b,
                purchase_order_id=po.id)
            db.flush()
            pay = (inv.total * Decimal(pay_ratio)).quantize(Decimal("1"))
            if pay > 0:
                purchases.post_payment(db, user_id=None, supplier_id=party[supplier_key], method=PaymentMethod.TRANSFER,
                                       amount=pay, allocations=[AllocationInput(inv.id, pay)],
                                       reference="Pago demo", idempotency_key=None)

    # Precios de compra IVA incluido = costo * 1.1 (IVA10) o * 1.05 (IVA5).
    buy("prov-3", [("BEB-AGUA-500", 300, 3080), ("BEB-GASEOSA-2L", 200, 10450), ("BEB-JUGO-1L", 150, 7920)],
        1, "001-001-0004521", 0.6)
    buy("prov-1", [("ALM-ARROZ-1KG", 400, 6615), ("ALM-AZUCAR-1KG", 300, 5355), ("ALM-ACEITE-900", 200, 12390),
                   ("ALM-YERBA-500", 250, 10290)], 1, "002-001-0000877", 1)
    buy("prov-2", [("LIM-LAVANDINA-1L", 200, 3960), ("LIM-DETERGENTE-750", 200, 7590),
                   ("LIM-JABON-POLVO-1KG", 100, 16940)], 0.5, None, 0)

    def sell(customer_key, items, condition, stage, paid_ratio=0):
        order = sales.create_order(db, user_id=None, customer_id=party[customer_key], warehouse_id=wh,
                                   payment_condition=condition, notes="Venta de ejemplo (demo)",
                                   lines=[sales.LineInput(pid[sku], Decimal(q)) for sku, q in items])
        db.flush()
        if stage == "draft":
            return
        sales.confirm(db, order.id, None)
        if stage == "confirmed":
            return
        sales.deliver(db, order.id, None)
        inv = receivables.invoice_order(db, order.id, None)
        db.flush()
        paid = (inv.total * Decimal(paid_ratio)).quantize(Decimal("1"))
        if paid > 0:
            receivables.post_receipt(db, user_id=None, customer_id=party[customer_key],
                                     method=PaymentMethod.CASH if condition == PaymentCondition.CASH else PaymentMethod.TRANSFER,
                                     amount=paid, allocations=[AllocationInput(inv.id, paid)], reference="Cobro demo",
                                     idempotency_key=None)

    cash_c, credit_c = PaymentCondition.CASH, PaymentCondition.CREDIT
    sell("cli-6", [("BEB-AGUA-500", 24), ("ALM-YERBA-500", 6), ("SRV-ENTREGA", 1)], cash_c, "invoiced", 1)
    sell("cli-4", [("ALM-ARROZ-1KG", 20), ("ALM-AZUCAR-1KG", 20), ("ALM-ACEITE-900", 10)], cash_c, "invoiced", 1)
    sell("cli-3", [("BEB-GASEOSA-2L", 60), ("BEB-JUGO-1L", 40), ("LIM-DETERGENTE-750", 30)], credit_c, "invoiced", 0.5)
    sell("cli-1", [("ALM-ARROZ-1KG", 80), ("ALM-YERBA-500", 40), ("LIM-LAVANDINA-1L", 30)], credit_c, "invoiced", 0)
    sell("cli-5", [("BEB-AGUA-500", 48), ("BEB-GASEOSA-2L", 24), ("SRV-INSTALACION", 2)], credit_c, "invoiced", 1)
    sell("mix-1", [("ALM-AZUCAR-1KG", 50), ("ALM-ACEITE-900", 30)], credit_c, "confirmed")
    sell("cli-2", [("LIM-JABON-POLVO-1KG", 10), ("BEB-JUGO-1L", 12)], cash_c, "draft")


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
        db.flush()
        _seed_operations(db)
    return True
