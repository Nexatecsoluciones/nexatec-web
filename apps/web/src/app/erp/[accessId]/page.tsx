"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Stat, Table, useErp, useLoad } from "@/components/erp";
import { day, money, quantity, type Dashboard } from "@/lib/erp";

export default function TableroPage() {
  const { client } = useErp();
  const [range, setRange] = useState<{ from: string; to: string }>({ from: "", to: "" });
  const { data, error } = useLoad(
    () => client.get<Dashboard>("/dashboard", { date_from: range.from, date_to: range.to }),
    [range.from, range.to],
  );

  const max = data ? Math.max(1, ...data.sales_by_day.map((d) => Number(d.sales_total))) : 1;

  return (
    <>
      <PageTitle
        title="Tablero"
        subtitle={data ? `${day(data.date_from)} al ${day(data.date_to)} · montos en guaranies` : undefined}
        actions={
          <div className="flex gap-2">
            <Field label="Desde"><Input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
            <Field label="Hasta"><Input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
          </div>
        }
      />
      {error && <Notice>{error}</Notice>}
      {!data ? (
        !error && <p className="text-nx-muted">Cargando...</p>
      ) : (
        <div className="flex flex-col gap-5">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Ventas (IVA incl.)" value={money(data.sales_total)} hint={data.definitions.sales_total} />
            <Stat label="Ventas netas" value={money(data.sales_net)} hint={data.definitions.sales_net} />
            <Stat label="Margen bruto" value={`${money(data.gross_margin)} (${data.gross_margin_pct}%)`} hint={data.definitions.gross_margin} />
            <Stat label="Ticket promedio" value={money(data.average_ticket)} hint={data.definitions.average_ticket} />
            <Stat label="Cobrar (abierto)" value={money(data.receivables_open)} hint={data.definitions.receivables_open} />
            <Stat label="Cobrar vencido" value={money(data.receivables_overdue)} hint={data.definitions.receivables_overdue} />
            <Stat label="Pagar (abierto)" value={money(data.payables_open)} hint={data.definitions.payables_open} />
            <Stat label="Pagar en 7 dias" value={money(data.payables_due_7d)} hint={data.definitions.payables_due_7d} />
            <Stat label="Caja y bancos" value={money(data.cash_and_bank)} hint={data.definitions.cash_and_bank} />
            <Stat label="Valor de stock" value={money(data.stock_value)} hint={data.definitions.stock_value} />
            <Stat label="Sin stock" value={String(data.out_of_stock)} hint={data.definitions.out_of_stock} />
            <Stat label="Pedidos pendientes" value={`${data.pending_orders.count} · ${money(data.pending_orders.value)}`} hint={data.definitions.pending_orders} />
          </div>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <Panel title="Ventas por dia">
              <div className="flex h-40 items-end gap-[2px]" aria-label="Grafico de ventas por dia">
                {data.sales_by_day.map((d) => (
                  <div key={d.date} title={`${day(d.date)}: ${money(d.sales_total)}`}
                    className="flex-1 rounded-t bg-nx-accent/70"
                    style={{ height: `${Math.max(2, (Number(d.sales_total) / max) * 100)}%` }} />
                ))}
              </div>
            </Panel>
            <Panel title="Productos mas vendidos (neto)">
              <Table
                rows={data.top_products}
                rowKey={(r) => r.sku}
                empty="Sin ventas entregadas en el rango."
                columns={[
                  { header: "Producto", cell: (r) => r.name },
                  { header: "Cantidad", cell: (r) => quantity(r.quantity), align: "right" },
                  { header: "Venta neta", cell: (r) => money(r.net_sales), align: "right" },
                ]}
              />
            </Panel>
          </div>
          <p className="text-xs text-nx-muted">Pasa el cursor sobre cada indicador para ver como se calcula.</p>
        </div>
      )}
    </>
  );
}
