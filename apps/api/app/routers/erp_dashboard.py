"""Tablero de gestion del ERP: KPIs de ventas, margen, cartera, pagos,
caja/bancos y stock. Solo lectura, todo agregado en la base (sin traer
filas a memoria). Cada metrica viaja con su definicion: que documentos
cuenta, si incluye IVA y a que fecha de corte."""

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Date, cast, func, select

from app.security.erp_context import ErpContext, get_erp_context
from app.services.receivables import local_today
from app.tenant_models.accounting import AccountMapping, JournalLine
from app.tenant_models.core import Product
from app.tenant_models.inventory import StockBalance
from app.tenant_models.purchases import SupplierInvoice
from app.tenant_models.receivables import InvoiceStatus, SalesInvoice
from app.tenant_models.sales import SalesOrder, SalesOrderLine, SalesOrderStatus

router = APIRouter(prefix="/api/erp/{system_access_id}", tags=["erp", "dashboard"])

D0 = Decimal("0")

DEFINITIONS = {
    "sales_total": "Total de facturas internas vigentes emitidas en el rango, IVA incluido.",
    "sales_net": "Mismo universo, neto de IVA (gravado 10 + gravado 5 + exento).",
    "average_ticket": "Total facturado / cantidad de facturas, IVA incluido.",
    "gross_margin": "Ventas netas de IVA menos costo congelado al entregar, de pedidos entregados en el rango.",
    "receivables_open": "Saldo pendiente de facturas internas vigentes, a hoy.",
    "receivables_overdue": "Parte del saldo pendiente con vencimiento anterior a hoy.",
    "payables_open": "Saldo pendiente de facturas de proveedor vigentes, a hoy.",
    "payables_due_7d": "Saldo de proveedores que vence en los proximos 7 dias (incluye vencido).",
    "cash_and_bank": "Saldo contable de las cuentas mapeadas como CASH y BANK.",
    "stock_value": "Existencia x costo promedio vigente.",
    "out_of_stock": "Productos que manejan stock sin disponible (existencia - reservado <= 0).",
    "pending_orders": "Pedidos confirmados todavia no entregados.",
}


@router.get("/dashboard")
def dashboard(ctx: ErpContext = Depends(get_erp_context), date_from: date | None = None, date_to: date | None = None):
    db = ctx.db
    today = local_today(db)
    df = date_from or today.replace(day=1)
    dt = date_to or today
    if df > dt or (dt - df).days > 366:
        raise HTTPException(status_code=422, detail="Rango invalido (maximo un anio).")

    issued = (SalesInvoice.status == InvoiceStatus.ISSUED, SalesInvoice.issue_date >= df, SalesInvoice.issue_date <= dt)
    total, net, count = db.execute(select(
        func.coalesce(func.sum(SalesInvoice.total), 0),
        func.coalesce(func.sum(SalesInvoice.taxable_10 + SalesInvoice.taxable_5 + SalesInvoice.exempt), 0),
        func.count(SalesInvoice.id),
    ).where(*issued)).one()

    delivered = (SalesOrder.status == SalesOrderStatus.DELIVERED,
                 cast(SalesOrder.delivered_at, Date) >= df, cast(SalesOrder.delivered_at, Date) <= dt)
    revenue, cost = db.execute(
        select(func.coalesce(func.sum(SalesOrderLine.line_net), 0),
               func.coalesce(func.sum(SalesOrderLine.quantity * func.coalesce(SalesOrderLine.unit_cost, 0)), 0))
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id).where(*delivered)
    ).one()
    revenue, cost = Decimal(revenue), Decimal(cost).quantize(Decimal("1"))
    margin = revenue - cost

    ar_open, ar_overdue = db.execute(select(
        func.coalesce(func.sum(SalesInvoice.balance_due), 0),
        func.coalesce(func.sum(SalesInvoice.balance_due).filter(SalesInvoice.due_date < today), 0),
    ).where(SalesInvoice.status == InvoiceStatus.ISSUED)).one()
    ap_open, ap_due = db.execute(select(
        func.coalesce(func.sum(SupplierInvoice.balance_due), 0),
        func.coalesce(func.sum(SupplierInvoice.balance_due).filter(SupplierInvoice.due_date <= today + timedelta(days=7)), 0),
    ).where(SupplierInvoice.status == InvoiceStatus.ISSUED)).one()

    cash_accounts = select(AccountMapping.account_id).where(AccountMapping.key.in_(["CASH", "BANK"]))
    cash = db.execute(select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
                      .where(JournalLine.account_id.in_(cash_accounts))).scalar_one()

    stock_value = db.execute(
        select(func.coalesce(func.sum(StockBalance.on_hand * Product.average_cost), 0))
        .join(Product, Product.id == StockBalance.product_id)
    ).scalar_one()
    available = (select(func.coalesce(func.sum(StockBalance.on_hand - StockBalance.reserved), 0))
                 .where(StockBalance.product_id == Product.id).scalar_subquery())
    out_of_stock = db.execute(select(func.count(Product.id)).where(
        Product.tracks_stock.is_(True), Product.is_active.is_(True), available <= 0)).scalar_one()

    pending_count, pending_value = db.execute(select(
        func.count(SalesOrder.id), func.coalesce(func.sum(SalesOrder.total), 0),
    ).where(SalesOrder.status == SalesOrderStatus.CONFIRMED)).one()

    top = db.execute(
        select(Product.sku, Product.name, func.sum(SalesOrderLine.quantity), func.sum(SalesOrderLine.line_net))
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id)
        .join(Product, Product.id == SalesOrderLine.product_id)
        .where(*delivered).group_by(Product.sku, Product.name)
        .order_by(func.sum(SalesOrderLine.line_net).desc()).limit(5)
    ).all()

    by_day = dict(db.execute(
        select(SalesInvoice.issue_date, func.sum(SalesInvoice.total)).where(*issued).group_by(SalesInvoice.issue_date)
    ).all())
    series = []
    day = df
    while day <= dt:
        series.append({"date": day, "sales_total": Decimal(by_day.get(day, 0))})
        day += timedelta(days=1)

    count = int(count)
    return {
        "date_from": df, "date_to": dt, "as_of": today, "currency": "PYG",
        "sales_total": Decimal(total), "sales_net": Decimal(net), "invoice_count": count,
        "average_ticket": (Decimal(total) / count).quantize(Decimal("1")) if count else D0,
        "gross_margin": margin, "gross_margin_pct": (margin * 100 / revenue).quantize(Decimal("0.1")) if revenue else D0,
        "receivables_open": Decimal(ar_open), "receivables_overdue": Decimal(ar_overdue),
        "payables_open": Decimal(ap_open), "payables_due_7d": Decimal(ap_due),
        "cash_and_bank": Decimal(cash), "stock_value": Decimal(stock_value).quantize(Decimal("1")),
        "out_of_stock": int(out_of_stock),
        "pending_orders": {"count": int(pending_count), "value": Decimal(pending_value)},
        "top_products": [{"sku": s, "name": n, "quantity": q, "net_sales": v} for s, n, q, v in top],
        "sales_by_day": series,
        "definitions": DEFINITIONS,
    }
