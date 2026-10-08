// Datos del sitio publico. Valores comerciales tomados del sitio vigente de
// NEXATEC (index.html del repo); nada inventado. Los datos legales de la
// empresa (razon social, RUC, domicilio) NO se completan hasta tenerlos
// verificados: los campos vacios no se muestran.

export const WHATSAPP_NUMBER = process.env.NEXT_PUBLIC_WHATSAPP_NUMBER ?? "595981813971";
export const CONTACT_EMAIL = "nexasolucionestec@gmail.com";

export const LEGAL = {
  legalName: process.env.NEXT_PUBLIC_LEGAL_NAME ?? "",
  ruc: process.env.NEXT_PUBLIC_LEGAL_RUC ?? "",
  address: process.env.NEXT_PUBLIC_LEGAL_ADDRESS ?? "",
  version: "2026-10-08 (borrador)",
};

export function whatsappUrl(message: string): string {
  return `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(message)}`;
}

export const isProduction = () => process.env.NEXATEC_ENV === "production";
export const publicUrl = () => process.env.NEXATEC_PUBLIC_URL ?? "https://www.nexatecpy.com";
