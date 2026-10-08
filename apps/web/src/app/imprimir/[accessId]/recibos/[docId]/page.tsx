"use client";

// Recibos de pago de una planilla de RR.HH., dos por hoja A4, con linea de
// firma "Recibi conforme". "Imprimir -> Guardar como PDF" genera el PDF.
// No son comprobantes tributarios. En una empresa de demostracion llevan
// la marca DEMO (datos ficticios).

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, day, erp, money, quantity, type HrPayroll } from "@/lib/erp";

interface Company { legal_name: string; trade_name: string | null; ruc: string | null; ruc_dv: string | null; ruc_is_fictitious: boolean; address: string | null }

export default function RecibosPage() {
  const { accessId, docId } = useParams<{ accessId: string; docId: string }>();
  const [company, setCompany] = useState<Company | null>(null);
  const [p, setP] = useState<HrPayroll | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = erp(accessId);
    Promise.all([c.get<Company>("/company"), c.get<HrPayroll>(`/hr/payrolls/${docId}`)])
      .then(([co, pay]) => { setCompany(co); setP(pay); })
      .catch((err) => setError(err instanceof ApiError ? err.message : "No se pudo cargar la planilla."));
  }, [accessId, docId]);

  if (error) return <main className="p-10 text-center">{error}</main>;
  if (!company || !p) return <main className="p-10 text-center text-gray-500">Cargando...</main>;
  const ruc = company.ruc ? `RUC ${company.ruc}-${company.ruc_dv}` : "";

  return (
    <main className="min-h-screen bg-white text-[13px] text-black">
      <style>{`
        @page { size: A4; margin: 10mm; }
        @media print { .no-print { display: none !important; } .recibo { break-inside: avoid; } }
        .wm { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
        .wm span { transform: rotate(-30deg); font-size: 64px; font-weight: 800; color: rgba(200, 30, 30, 0.14); }
      `}</style>
      {company.ruc_is_fictitious && <div className="wm" aria-hidden="true"><span>DEMO · DATOS FICTICIOS</span></div>}
      <div className="no-print flex items-center justify-between border-b p-3">
        <p className="text-sm">Planilla {p.number} · {p.lines.length} recibo(s){p.status !== "CLOSED" && " · BORRADOR (montos pueden cambiar)"}</p>
        <button onClick={() => window.print()} className="rounded bg-[#0b4b42] px-4 py-2 text-sm font-bold text-white">Imprimir / Guardar PDF</button>
      </div>
      <div className="mx-auto max-w-[190mm]">
        {p.lines.map((l) => (
          <section key={l.employee_id} className="recibo relative my-3 rounded border border-gray-400 p-4" style={{ minHeight: "128mm" }}>
            <header className="flex justify-between border-b border-gray-300 pb-2">
              <div>
                <p className="font-extrabold">{company.legal_name}</p>
                <p className="text-xs">{[ruc, company.address].filter(Boolean).join(" · ")}</p>
              </div>
              <div className="text-right">
                <p className="font-extrabold">RECIBO DE PAGO</p>
                <p className="text-xs">Planilla {p.number}{p.status !== "CLOSED" && " (borrador)"}</p>
                <p className="text-xs">Periodo {day(p.period_start)} al {day(p.period_end)}</p>
              </div>
            </header>
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
              <p><strong>Trabajador:</strong> {l.employee_name}</p>
              <p><strong>C.I.:</strong> {l.national_id}</p>
              <p><strong>Dias trabajados:</strong> {l.days_worked}{l.unexcused_absences > 0 && ` · faltas: ${l.unexcused_absences}`}</p>
              <p><strong>Forma de cobro:</strong> {l.pay_method === "TRANSFER" ? `Transferencia ${l.bank_account ?? ""}` : "Efectivo"}</p>
            </div>
            <table className="mt-3 w-full text-xs">
              <tbody>
                {[
                  [`Horas normales (${quantity(l.regular_hours)} h x ${money(l.hourly_rate)})`, l.base_amount],
                  ...(Number(l.overtime_hours) > 0 ? [[`Horas extra (${quantity(l.overtime_hours)} h, con recargo)`, l.overtime_amount]] : []),
                  ...(Number(l.bonus_amount) > 0 ? [["Premio por asistencia", l.bonus_amount]] : []),
                  ...(Number(l.adjustment) !== 0 ? [[`Ajuste${l.adjustment_note ? `: ${l.adjustment_note}` : ""}`, l.adjustment]] : []),
                ].map(([k, v]) => (
                  <tr key={k} className="border-b border-gray-200"><td className="py-1">{k}</td><td className="py-1 text-right">{money(v)}</td></tr>
                ))}
                <tr className="font-bold"><td className="py-1">Total haberes</td><td className="py-1 text-right">{money(l.gross)}</td></tr>
                {Number(l.ips_employee) > 0 && <tr><td className="py-1">Descuento IPS (aporte obrero)</td><td className="py-1 text-right">- {money(l.ips_employee)}</td></tr>}
                {Number(l.advances) > 0 && <tr><td className="py-1">Adelantos del periodo</td><td className="py-1 text-right">- {money(l.advances)}</td></tr>}
                <tr className="border-t-2 border-black text-sm font-extrabold"><td className="py-1">NETO A COBRAR</td><td className="py-1 text-right">{money(l.net)}</td></tr>
              </tbody>
            </table>
            <div className="absolute inset-x-4 bottom-4 grid grid-cols-2 gap-10 text-center text-xs">
              <div><div className="border-t border-black pt-1">Firma del empleador</div></div>
              <div><div className="border-t border-black pt-1">Recibi conforme: {l.employee_name}</div></div>
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
