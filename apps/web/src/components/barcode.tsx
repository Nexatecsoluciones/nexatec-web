"use client";

import JsBarcode from "jsbarcode";
import { useEffect, useRef } from "react";

// Codigo de barras en SVG (nitido en cualquier impresora). Code 128 para
// codigos internos; EAN-13/UPC-A/EAN-8 para codigos del fabricante.
const FORMAT: Record<string, string> = { CODE128: "CODE128", EAN13: "EAN13", UPCA: "UPC", EAN8: "EAN8" };

export function Barcode({ value, symbology = "CODE128", height = 40, width = 1.6, fontSize = 12 }: {
  value: string; symbology?: string; height?: number; width?: number; fontSize?: number;
}) {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    try {
      JsBarcode(ref.current, value, { format: FORMAT[symbology] ?? "CODE128", height, width, fontSize, margin: 0, displayValue: true });
    } catch {
      // Codigo invalido para ese formato: no rompe la pagina; se muestra el texto.
      ref.current.innerHTML = "";
    }
  }, [value, symbology, height, width, fontSize]);
  return <svg ref={ref} role="img" aria-label={`Codigo de barras ${value}`} />;
}
