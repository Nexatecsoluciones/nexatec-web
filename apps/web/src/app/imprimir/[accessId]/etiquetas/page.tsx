"use client";

// Etiquetas de productos con codigo de barras: hoja A4 (grilla) o rollo
// termico 58/80 mm. ?ids=a,b,c&f=a4|58|80&c=copias. Si un producto no tiene
// codigo, la etiqueta lo avisa (se genera uno interno desde Productos).

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Barcode } from "@/components/barcode";
import { money } from "@/lib/erp";

interface Label { product_id: string; sku: string; name: string; sale_price: string; code: string | null; symbology: string | null }

function Labels() {
  const { accessId } = useParams<{ accessId: string }>();
  const sp = useSearchParams();
  const ids = (sp.get("ids") ?? "").split(",").filter(Boolean).slice(0, 500);
  const [fmt, setFmt] = useState(sp.get("f") ?? "a4");
  const [copies, setCopies] = useState(Math.min(50, Math.max(1, Number(sp.get("c") ?? 1) || 1)));
  const [rows, setRows] = useState<Label[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ids.length) return;
    const qs = ids.map((i) => `ids=${encodeURIComponent(i)}`).join("&");
    fetch(`/api/erp/${accessId}/products-labels?${qs}`, { credentials: "include" })
      .then(async (r) => (r.ok ? setRows(await r.json()) : setError((await r.json().catch(() => ({}))).detail ?? "No se pudo cargar.")))
      .catch(() => setError("No se pudo cargar."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessId, sp]);

  if (!ids.length) return <main className="p-6">Elegi productos desde Productos &gt; Imprimir etiquetas.</main>;
  if (error) return <main className="p-6">{error}</main>;
  if (!rows) return <main className="p-6 text-gray-500">Cargando...</main>;
  const items = rows.flatMap((r) => Array.from({ length: copies }, (_, i) => ({ ...r, k: `${r.product_id}-${i}` })));
  const thermal = fmt !== "a4";
  const w = fmt === "58" ? "54mm" : "76mm";

  return (
    <main className="min-h-screen bg-white text-black">
      <style>{`
        @page { size: ${thermal ? `${fmt}mm auto` : "A4"}; margin: ${thermal ? "1mm" : "8mm"}; }
        @media print { .no-print { display: none !important; } }
        .grid-a4 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2mm; }
        .lbl { border: 1px dashed #bbb; padding: 2mm; text-align: center; break-inside: avoid; overflow: hidden; }
        .lbl svg { display: block; margin: 0 auto; max-width: 100%; height: auto; }
        @media print { .lbl { border-color: transparent; } }
      `}</style>
      <div className="no-print flex flex-wrap items-center gap-3 border-b p-3 text-sm">
        <label>Formato <select className="rounded border px-2 py-1" value={fmt} onChange={(e) => setFmt(e.target.value)}>
          <option value="a4">Hoja A4 (3 por fila)</option><option value="80">Termica 80 mm</option><option value="58">Termica 58 mm</option>
        </select></label>
        <label>Copias por producto <input className="w-16 rounded border px-2 py-1" type="number" min={1} max={50} value={copies}
          onChange={(e) => setCopies(Math.min(50, Math.max(1, Number(e.target.value) || 1)))} /></label>
        <span className="text-gray-500">{items.length} etiqueta(s)</span>
        <button onClick={() => window.print()} className="ml-auto rounded bg-[#0b4b42] px-4 py-2 font-bold text-white">Imprimir</button>
      </div>
      <div className={thermal ? "mx-auto flex flex-col gap-1" : "grid-a4 mx-auto max-w-[194mm] p-2"} style={thermal ? { width: w } : undefined}>
        {items.map((r) => (
          <div key={r.k} className="lbl">
            <p className="truncate text-[11px] font-bold leading-tight">{r.name}</p>
            <p className="text-sm font-extrabold">{money(r.sale_price)}</p>
            {r.code ? <Barcode value={r.code} symbology={r.symbology ?? "CODE128"} height={thermal ? 34 : 38} width={1.4} fontSize={11} />
              : <p className="py-3 text-[10px] text-red-600">Sin codigo de barras: generalo en Productos</p>}
            <p className="text-[9px] text-gray-600">{r.sku}</p>
          </div>
        ))}
      </div>
    </main>
  );
}

export default function EtiquetasPage() {
  return <Suspense fallback={<main className="p-6">Cargando...</main>}><Labels /></Suspense>;
}
