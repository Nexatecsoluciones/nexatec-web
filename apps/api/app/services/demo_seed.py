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
    _seed_crm(db, party)
    _seed_hr(db)


def _seed_crm(db: Session, party: dict) -> None:
    """Embudo de ejemplo: prospectos ficticios (telefonos 000, emails
    .invalid), oportunidades en distintas etapas y actividades pendientes."""
    from datetime import datetime, timedelta, timezone

    from app.services import crm
    from app.tenant_models.crm import ActivityKind, OpportunityStage

    now = datetime.now(timezone.utc)
    leads = {}
    for key, contact, company, source in [
        ("lead-1", "Carlos Ficticio", "Ferreteria Inventada S.A.", "WhatsApp"),
        ("lead-2", "Laura Ejemplo", "Farmacia de Prueba", "Sitio web"),
        ("lead-3", "Pedro Simulado", None, "Referido"),
    ]:
        leads[key] = crm.create_lead(db, user_id=None, owner_user_id=None, contact_name=contact, company_name=company,
                                     email=f"{key}@ejemplo.invalid", phone="(000) 000-000", source=source,
                                     notes="Prospecto ficticio (demo)").id

    def opp(title, amount, stage, party_key=None, lead_key=None, days=30, close=None):
        o = crm.create_opportunity(db, user_id=None, owner_user_id=None, title=title,
                                   party_id=party[party_key] if party_key else None,
                                   lead_id=leads[lead_key] if lead_key else None, amount=Decimal(amount),
                                   expected_close_date=(now + timedelta(days=days)).date(), notes=None)
        if stage != OpportunityStage.NEW:
            crm.move_stage(db, o.id, stage, None)
        if close == "won":
            crm.win(db, o.id)
        elif close == "lost":
            crm.lose(db, o.id, "Eligio otro proveedor (demo)")
        return o.id

    q = OpportunityStage
    o1 = opp("Provision mensual de bebidas", 12_000_000, q.NEGOTIATION, party_key="cli-3", days=10)
    opp("Ampliacion de linea de limpieza", 4_500_000, q.PROPOSAL, party_key="cli-1", days=20)
    o3 = opp("Abastecimiento de almacen", 8_000_000, q.QUALIFIED, lead_key="lead-1", days=35)
    opp("Pedido para sucursal nueva", 3_200_000, q.NEW, lead_key="lead-2", days=45)
    opp("Contrato anual de bebidas", 6_000_000, q.NEGOTIATION, party_key="cli-5", close="won")
    opp("Insumos de limpieza", 2_000_000, q.PROPOSAL, party_key="cli-2", close="lost")

    for kind, subject, opp_id, lead_key, hours in [
        (ActivityKind.CALL, "Confirmar volumen mensual", o1, None, -20),
        (ActivityKind.MEETING, "Visita para presentar catalogo", o3, None, 48),
        (ActivityKind.WHATSAPP, "Enviar lista de precios", None, "lead-2", 4),
        (ActivityKind.TASK, "Preparar propuesta escrita", o1, None, 24),
    ]:
        crm.create_activity(db, user_id=None, owner_user_id=None, kind=kind, subject=subject, notes=None, party_id=None,
                            lead_id=leads[lead_key] if lead_key else None, opportunity_id=opp_id,
                            due_at=now + timedelta(hours=hours))


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


def _seed_hr(db: Session) -> None:
    """RR.HH. de ejemplo: personas FICTICIAS (C.I. serie 9991xxx), dos obras,
    la quincena anterior liquidada y cerrada y la actual en curso, con
    tardanzas, extras pendientes, una falta, una ausencia justificada y
    adelantos. Todo con los servicios reales."""
    from datetime import time, timedelta

    from app.services import hr
    from app.services.receivables import local_today
    from app.tenant_models.hr import (
        AbsenceKind, AttendanceSource, HrCategory, HrSite, HrSiteDaySchedule, PayMethod,
    )

    cfg = hr.settings(db)
    cfg.attendance_bonus_enabled = True
    cats = {
        "OFICIAL": HrCategory(id=_id("hrcat-of"), code="OFICIAL", default_trade="Albañil", hourly_rate=Decimal(14000)),
        "AYUDANTE": HrCategory(id=_id("hrcat-ay"), code="AYUDANTE", default_trade="Ayudante", hourly_rate=Decimal(11000)),
        "CAPATAZ": HrCategory(id=_id("hrcat-ca"), code="CAPATAZ", default_trade="Capataz", hourly_rate=Decimal(19000)),
    }
    db.add_all(cats.values())
    s1 = HrSite(id=_id("hrsite-1"), code="OB-01", name="Edificio Ejemplo Centro (demo)", client_name="Cliente Ficticio S.A.",
                location="Asuncion (ficticia)", start_time=time(7, 0), end_time=time(15, 0), workdays=[0, 1, 2, 3, 4],
                tolerance_minutes=10)
    s1.day_schedules.append(HrSiteDaySchedule(id=_id("hrsite-1-sat"), weekday=5, start_time=time(7, 0), end_time=time(12, 0)))
    s2 = HrSite(id=_id("hrsite-2"), code="OB-02", name="Deposito Ejemplo Ruta (demo)", client_name="Otro Cliente Ficticio",
                location="Luque (ficticia)", start_time=time(7, 30), end_time=time(16, 30), workdays=[0, 1, 2, 3, 4],
                tolerance_minutes=10)
    db.add_all([s1, s2])
    db.flush()

    today = local_today(db)
    cur_start, _ = hr.period_for(today, cfg)
    prev_start, prev_end = hr.period_for(cur_start - timedelta(days=1), cfg)
    hired = prev_start - timedelta(days=30)
    people = [
        ("9991001", "Gomez Ejemplo", "Juan Carlos", "CAPATAZ", s1, PayMethod.TRANSFER),
        ("9991002", "Benitez Ficticio", "Pedro", "OFICIAL", s1, PayMethod.CASH),
        ("9991003", "Ortiz Demo", "Ramon", "OFICIAL", s1, PayMethod.CASH),
        ("9991004", "Ayala Prueba", "Luis", "AYUDANTE", s1, PayMethod.CASH),
        ("9991005", "Vera Simulado", "Marcos", "AYUDANTE", s1, PayMethod.TRANSFER),
        ("9991006", "Duarte Ejemplo", "Ana", "OFICIAL", s2, PayMethod.TRANSFER),
        ("9991007", "Rojas Ficticio", "Hugo", "AYUDANTE", s2, PayMethod.CASH),
        ("9991008", "Cabrera Demo", "Nelson", "OFICIAL", s2, PayMethod.CASH),
    ]
    emps = []
    for i, (ci, ln, fn, cat, site, pm) in enumerate(people):
        e = hr.create_employee(db, {
            "national_id": ci, "last_names": ln, "first_names": fn, "category_id": cats[cat].id,
            "phone": "(000) 000-000", "pay_method": pm, "bank_account": f"000-{ci}" if pm == PayMethod.TRANSFER else None,
            "bonus_per_hour": Decimal(500), "ips_entry": hired if i != 4 else None,  # uno sin IPS (jornalero)
        }, site.id, hired, None)
        emps.append((e, site))

    def work(d, e, site, tin=time(7, 0), tout=None):
        exc = hr.day_exception(site, d)
        end = exc.end_time if exc else site.end_time
        start = exc.start_time if exc else site.start_time
        hr.record_attendance(db, employee_id=e.id, site_id=site.id, work_date=d, time_in=tin if tin != time(7, 0) else start,
                             time_out=tout or end, source=AttendanceSource.MANUAL, user_id=None)

    d = prev_start
    while d < today:
        for idx, (e, site) in enumerate(emps):
            if not hr.is_workday(site, d):
                continue
            # Casos de ejemplo: una falta sin justificar (periodo actual), una
            # tardanza, extras y una ausencia justificada.
            if idx == 3 and d == cur_start + timedelta(days=1) and d < today:
                continue
            if idx == 7 and prev_start + timedelta(days=2) <= d <= prev_start + timedelta(days=3):
                continue
            tin = time(7, 25) if (idx == 2 and d.day % 4 == 0) else time(7, 0)
            tout = time(16, 0) if (idx == 0 and d.weekday() == 2) else None
            work(d, e, site, tin, tout)
        d += timedelta(days=1)
    e7, _ = emps[7]
    hr.create_absence(db, employee_id=e7.id, start=prev_start + timedelta(days=2), end=prev_start + timedelta(days=3),
                      kind=AbsenceKind.MEDICAL, notes="Reposo (ejemplo)", user_id=None)
    # Extras del periodo anterior: aprobadas, para poder cerrarlo.
    from sqlalchemy import select as _select

    from app.tenant_models.hr import HrAttendance, OvertimeStatus
    for a in db.execute(_select(HrAttendance).where(HrAttendance.work_date <= prev_end,
                                                    HrAttendance.overtime_status == OvertimeStatus.PENDING)).scalars():
        hr.decide_overtime(db, a.id, True)
    for i, amount in ((1, 150000), (3, 100000), (6, 200000)):
        e, site = emps[i]
        hr.create_advance(db, employee_id=e.id, site_id=site.id, advance_date=prev_start + timedelta(days=5),
                          amount=Decimal(amount), pay_method=PayMethod.CASH, notes="Adelanto (ejemplo)", user_id=None)
    if cur_start < today:
        e, site = emps[2]
        hr.create_advance(db, employee_id=e.id, site_id=site.id, advance_date=cur_start, amount=Decimal(120000),
                          pay_method=PayMethod.CASH, notes="Adelanto (ejemplo)", user_id=None)
    payroll = hr.create_payroll(db, prev_start, prev_end, None, None)
    hr.close_payroll(db, payroll.id, None)
