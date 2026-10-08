"use client";

// Vista de impresion de comprobantes internos (factura / nota de credito).
// "Imprimir -> Guardar como PDF" del navegador genera el PDF. La marca de
// agua y el aviso SALEN IMPRESOS: estos documentos no tienen validez
// tributaria mientras SIFEN no este habilitado (ver docs/ERP_DATA_MODEL.md).

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, day, erp, money, quantity, type CreditNote, type Invoice, type Party, type SalesOrder } from "@/lib/erp";

interface Company {
  legal_name: string; trade_name: string | null; ruc: string | null; ruc_dv: string | null; ruc_is_fictitious: boolean;
  address: string | null; phone: string | null; email: string | null;
}
interface CreditNoteFull extends CreditNote {
  customer_id: string; taxable_10: string; vat_10: string; taxable_5: string; vat_5: string; exempt: string;
  lines: { order_line_no: number; description: string; quantity: string; tax_rate: string; line_total: string }[];
}
type Doc = { title: string; number: string; date: string; customerId: string; notice: string | null;
  rows: { description: string; quantity: string; unit: string | null; rate: string; total: string }[];
  taxable_10: string; vat_10: string; taxable_5: string; vat_5: string; exempt: string; total: string; extra?: string };

const WATERMARK = "DOCUMENTO DE SIMULACIÓN — SIN VALIDEZ TRIBUTARIA";

export default function PrintPage() {
  const { accessId, kind, docId } = useParams<{ accessId: string; kind: string; docId: string }>();
  const [company, setCompany] = useState<Company | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [customer, setCustomer] = useState<Party | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = erp(accessId);
    (async () => {
      try {
        setCompany(await c.get<Company>("/company"));
        let d: Doc;
        if (kind === "factura") {
          const inv = await c.get<Invoice>(`/invoices/${docId}`);
          const order = await c.get<SalesOrder>(`/sales/orders/${inv.order_id}`);
          d = { title: "FACTURA (comprobante interno)", number: inv.number, date: inv.issue_date, customerId: inv.customer_id,
            notice: inv.legal_notice, taxable_10: inv.taxable_10, vat_10: inv.vat_10, taxable_5: inv.taxable_5, vat_5: inv.vat_5,
            exempt: inv.exempt, total: inv.total,
            extra: `Condicion: ${inv.payment_condition === "CREDIT" ? `Credito · vence ${day(inv.due_date)}` : "Contado"}`,
            rows: order.lines.map((l) => ({ description: l.description, quantity: l.quantity, unit: l.unit_price, rate: l.tax_rate, total: l.line_total })) };
        } else if (kind === "nota-credito") {
          const n = await c.get<CreditNoteFull>(`/credit-notes/${docId}`);
          const inv = await c.get<Invoice>(`/invoices/${n.invoice_id}`);
          d = { title: "NOTA DE CREDITO (comprobante interno)", number: n.number, date: n.issue_date, customerId: n.customer_id,
            notice: n.legal_notice, taxable_10: n.taxable_10, vat_10: n.vat_10, taxable_5: n.taxable_5, vat_5: n.vat_5,
            exempt: n.exempt, total: n.total, extra: `Sobre factura ${inv.number} · Motivo: ${n.reason}`,
            rows: n.lines.map((l) => ({ description: l.description, quantity: l.quantity, unit: null, rate: l.tax_rate, total: l.line_total })) };
        } else {
          throw new ApiError(404, "Tipo de documento desconocido.");
        }
        setDoc(d);
        setCustomer(await c.get<Party>(`/parties/${d.customerId}`));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "No se pudo cargar el documento.");
      }
    })();
  }, [accessId, kind, docId]);

  if (error) return <main className="p-10 text-center">{error}</main>;
  if (!company || !doc) return <main className="p-10 text-center text-gray-500">Cargando...</main>;

  const ruc = company.ruc ? `${company.ruc}-${company.ruc_dv}` : "sin RUC cargado";
  return (
    <div className="print-doc min-h-screen bg-white text-[#10302b]">
      <style>{`
        @media print { .no-print { display: none !important; } @page { size: A4; margin: 14mm; } }
        .print-doc { -webkit-print-color-adjust: exact; print-color-adjust: exact; font-family: Arial, sans-serif; }
        .watermark { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; z-index: 0; }
        .watermark span { transform: rotate(-30deg); font-size: 46px; font-weight: 800; color: rgba(200, 30, 30, 0.16); text-align: center; line-height: 1.2; }
      `}</style>
      <div className="watermark" aria-hidden="true"><span>{WATERMARK}</span></div>
      <div className="no-print flex justify-end gap-2 bg-gray-100 p-3">
        <button onClick={() => window.print()} className="rounded bg-[#0b4b42] px-4 py-2 text-sm font-bold text-white">Imprimir / Guardar PDF</button>
      </div>
      <main className="relative z-10 mx-auto max-w-[800px] p-8 text-sm">
        <p className="mb-4 border-2 border-red-700 p-2 text-center font-bold text-red-700">{doc.notice ?? WATERMARK}</p>
        <header className="flex justify-between gap-6 border-b pb-4">
          <div>
            <h1 className="text-lg font-extrabold">{company.legal_name}</h1>
            <p>RUC: {ruc}{company.ruc_is_fictitious && " (FICTICIO — no es un RUC real)"}</p>
            {company.address && <p>{company.address}</p>}
            {(company.phone || company.email) && <p>{[company.phone, company.email].filter(Boolean).join(" · ")}</p>}
          </div>
          <div className="text-right">
            <p className="font-extrabold">{doc.title}</p>
            <p className="text-lg font-bold">{doc.number}</p>
            <p>Fecha: {day(doc.date)}</p>
            <p className="mt-1 text-xs">Sin timbrado: no emitido ante la DNIT</p>
          </div>
        </header>
        <section className="border-b py-3">
          <p><strong>Cliente:</strong> {customer?.legal_name ?? "-"}</p>
          <p><strong>RUC:</strong> {customer?.ruc ? `${customer.ruc}-${customer.ruc_dv}` : "-"}{customer?.ruc_status === "FICTITIOUS" && " (ficticio)"}</p>
          {doc.extra && <p>{doc.extra}</p>}
        </section>
        <table className="mt-3 w-full border-collapse text-left">
          <thead><tr className="border-b-2"><th className="py-1">Descripcion</th><th className="text-right">Cant.</th><th className="text-right">Precio</th><th className="text-right">IVA</th><th className="text-right">Importe</th></tr></thead>
          <tbody>
            {doc.rows.map((r, i) => (
              <tr key={i} className="border-b">
                <td className="py-1">{r.description}</td>
                <td className="text-right">{Number(r.quantity) ? quantity(r.quantity) : "-"}</td>
                <td className="text-right">{r.unit ? money(r.unit) : "-"}</td>
                <td className="text-right">{Number(r.rate) ? `${Number(r.rate)}%` : "Exenta"}</td>
                <td className="text-right">{money(r.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <section className="ml-auto mt-4 w-72">
          {[["Gravado 10%", doc.taxable_10], ["IVA 10%", doc.vat_10], ["Gravado 5%", doc.taxable_5], ["IVA 5%", doc.vat_5], ["Exento", doc.exempt]].map(([k, v]) => (
            <div key={k} className="flex justify-between"><span>{k}</span><span>{money(v)}</span></div>
          ))}
          <div className="mt-1 flex justify-between border-t-2 pt-1 text-base font-extrabold"><span>Total</span><span>{money(doc.total)}</span></div>
          <p className="mt-1 text-xs">Precios con IVA incluido.</p>
        </section>
        <footer className="mt-10 border-t pt-3 text-center text-xs text-gray-600">
          {WATERMARK}. Documento generado por NEXATEC ERP para gestion interna. No reemplaza la factura electronica (SIFEN).
        </footer>
      </main>
    </div>
  );
}
