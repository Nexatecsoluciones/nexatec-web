// Datos del sitio publico. Valores comerciales tomados del sitio vigente de
// NEXATEC (index.html del repo); nada inventado. Datos legales provistos por
// el titular el 2026-10-08 (RUC verificado con el DV modulo 11 de la SET).
// Domicilio informado por el titular el 2026-10-08.

export const WHATSAPP_NUMBER = process.env.NEXT_PUBLIC_WHATSAPP_NUMBER ?? "595981813971";
export const CONTACT_EMAIL = "nexasolucionestec@gmail.com";

export const LEGAL = {
  legalName: "Maria Nazareth Meyer",
  tradeName: "Nexatec PY",
  ruc: "5879897-8",
  activity: "Servicios generales, servicios digitales informaticos y de desarrollo web y sistemas",
  address: "Dr. Zacarias Arce entre Aparipy y Nazareth, Barrio Nazareth, Asuncion",
  version: "2026-10-08",
};

export function whatsappUrl(message: string): string {
  return `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(message)}`;
}

export const isProduction = () => process.env.NEXATEC_ENV === "production";
export const publicUrl = () => process.env.NEXATEC_PUBLIC_URL ?? "https://www.nexatecpy.com";
