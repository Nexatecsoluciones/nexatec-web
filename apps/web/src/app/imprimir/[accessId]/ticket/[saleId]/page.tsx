"use client";

// Ticket de venta rapida para impresora termica (80 mm; tambien sale en A4).
// Es un COMPROBANTE INTERNO: lleva "SIN VALIDEZ TRIBUTARIA" impreso y no
// imita el formato de una factura (sin timbrado, sin CDC, sin QR de SIFEN).

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, erp, methodLabel, money, quantity, type Invoice, type Party, type PosSale, type SalesOrder } from "@/lib/erp";

interface Company { legal_name: string; trade_name: string | null; ruc: string | null; ruc_dv: string | null; ruc_is_fictitious: boolean; address: string | null; phone: string | null }

export default function TicketPage() {
  const { accessId, saleId } = useParams<{ accessId: string; saleId: string }>();
  const [d, setD] = useState<{ company: Company; sale: PosSale; inv: Invoice; order: SalesOrder; customer: Party } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = erp(accessId);
    (async () => {
      try {
        const sale = await c.get<PosSale>(`/pos/sales/${saleId}`);
        const [company, inv, order, customer] = await Promise.all([
          c.get<Company>("/company"), c.get<Invoice>(`/invoices/${sale.invoice_id}`),
          c.get<SalesOrder>(`/sales/orders/${sale.order_id}`), c.get<Party>(`/parties/${sale.customer_id}`),
        ]);
        setD({ company, sale, inv, order, customer });
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "No se pudo cargar el ticket.");
      }
    })();
  }, [accessId, saleId]);

  if (error) return <main className="p-6 text-center">{error}</main>;
  if (!d) return <main className="p-6 text-center text-gray-500">Cargando...</main>;
  const { company, sale, inv, order, customer } = d;
  const when = new Date(sale.created_at).toLocaleString("es-PY", { timeZone: "America/Asuncion", dateStyle: "short", timeStyle: "short" });

  return (
    <main className="min-h-screen bg-white text-black">
      <style>{`
        @page { size: 80mm auto; margin: 3mm; }
        @media print { .no-print { display: none !important; } body { background: #fff; } }
        .t { width: 74mm; margin: 0 auto; font: 12px/1.35 ui-monospace, Menlo, Consolas, monospace; }
        .t hr { border: 0; border-top: 1px dashed #000; margin: 6px 0; }
      `}</style>
      <div className="no-print flex justify-center gap-2 border-b p-2">
        <button onClick={() => window.print()} className="rounded bg-[#0b4b42] px-4 py-2 text-sm font-bold text-white">Imprimir</button>
      </div>
      <div className="t py-3">
        <p className="text-center font-bold">{company.trade_name ?? company.legal_name}</p>
        <p className="text-center">{company.legal_name}</p>
        {company.ruc && <p className="text-center">RUC {company.ruc}-{company.ruc_dv}{company.ruc_is_fictitious && " (FICTICIO)"}</p>}
        {company.address && <p className="text-center">{company.address}</p>}
        <hr />
        <p className="text-center font-bold">COMPROBANTE INTERNO</p>
        <p className="text-center font-bold">SIN VALIDEZ TRIBUTARIA</p>
        <hr />
        <p>Nro: {inv.number}{sale.status === "VOIDED" && " - ANULADO"}</p>
        <p>Fecha: {when}</p>
        <p>Cliente: {customer.legal_name}{customer.ruc ? ` RUC ${customer.ruc}-${customer.ruc_dv}` : ""}</p>
        <p>Condicion: {inv.payment_condition === "CREDIT" ? "Credito" : "Contado"}</p>
        <hr />
        {order.lines.map((l) => (
          <div key={l.line_no}>
            <p>{l.description}</p>
            <p className="flex justify-between"><span>{quantity(l.quantity)} x {money(l.unit_price)}{Number(l.discount_pct) > 0 && ` -${Number(l.discount_pct)}%`}</span><span>{money(l.line_total)}</span></p>
          </div>
        ))}
        <hr />
        <p className="flex justify-between text-base font-bold"><span>TOTAL</span><span>{money(inv.total)}</span></p>
        {Number(inv.vat_10) > 0 && <p className="flex justify-between"><span>IVA 10% incluido</span><span>{money(inv.vat_10)}</span></p>}
        {Number(inv.vat_5) > 0 && <p className="flex justify-between"><span>IVA 5% incluido</span><span>{money(inv.vat_5)}</span></p>}
        {Number(inv.exempt) > 0 && <p className="flex justify-between"><span>Exentas</span><span>{money(inv.exempt)}</span></p>}
        <hr />
        {sale.payments.map((p, i) => <p key={i} className="flex justify-between"><span>{methodLabel(p.method)}</span><span>{money(p.amount)}</span></p>)}
        {Number(sale.change_given) > 0 && <p className="flex justify-between font-bold"><span>Vuelto</span><span>{money(sale.change_given)}</span></p>}
        {Number(sale.paid) < Number(sale.total) && <p className="flex justify-between font-bold"><span>Saldo a credito</span><span>{money(Number(sale.total) - Number(sale.paid))}</span></p>}
        <hr />
        <p className="text-center">Gracias por su compra</p>
        <p className="mt-1 text-center text-[10px]">Documento interno de gestion. No reemplaza a la factura electronica.</p>
      </div>
    </main>
  );
}
