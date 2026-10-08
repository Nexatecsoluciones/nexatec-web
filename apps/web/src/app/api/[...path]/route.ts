import { NextRequest, NextResponse } from "next/server";

/**
 * BFF same-origin: el navegador SOLO conoce https://nexatecpy.com/api/*.
 * Este handler reenvia server-side hacia la API interna
 * (NEXATEC_INTERNAL_API_URL, nunca NEXT_PUBLIC_ -- no llega al navegador).
 *
 * No es un open proxy: el destino siempre es
 * `${NEXATEC_INTERNAL_API_URL}/api/<path capturado por la ruta>`, nunca un
 * host arbitrario tomado de la request. Cualquier otra ruta fuera de
 * /api/* no pasa por aqui.
 */

const INTERNAL_API_URL = process.env.NEXATEC_INTERNAL_API_URL ?? "http://127.0.0.1:4301";

// Headers que nunca se reenvian tal cual (hop-by-hop o especificos del
// transporte navegador<->Next, no tienen sentido reenviados a la API).
const STRIP_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
  "accept-encoding",
]);

const STRIP_RESPONSE_HEADERS = new Set(["content-encoding", "transfer-encoding", "connection"]);

async function proxy(request: NextRequest, path: string[]): Promise<NextResponse> {
  const search = request.nextUrl.search;
  const targetUrl = `${INTERNAL_API_URL}/api/${path.join("/")}${search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIP_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });

  // Reconocimiento explicito de proxy: Cloudflare/cloudflared ya setean
  // estos headers en la conexion entrante a Next.js; se reenvian tal cual
  // hacia FastAPI para que sepa que el cliente real hablo por HTTPS y
  // cual es su IP real (safe_ip los sanitiza antes de loggear/guardar).
  headers.set("X-Forwarded-Proto", request.headers.get("x-forwarded-proto") ?? "https");
  headers.set("X-Forwarded-Host", request.headers.get("host") ?? "");
  const forwardedFor = request.headers.get("cf-connecting-ip") ?? request.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("X-Forwarded-For", forwardedFor);

  const hasBody = !["GET", "HEAD"].includes(request.method);

  const upstreamResponse = await fetch(targetUrl, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    redirect: "manual",
    cache: "no-store",
  });

  const responseHeaders = new Headers();
  upstreamResponse.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.append(key, value);
  });

  return new NextResponse(upstreamResponse.body, {
    status: upstreamResponse.status,
    headers: responseHeaders,
  });
}

type RouteParams = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, { params }: RouteParams) {
  return proxy(request, (await params).path);
}
export async function POST(request: NextRequest, { params }: RouteParams) {
  return proxy(request, (await params).path);
}
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  return proxy(request, (await params).path);
}
export async function PUT(request: NextRequest, { params }: RouteParams) {
  return proxy(request, (await params).path);
}
export async function DELETE(request: NextRequest, { params }: RouteParams) {
  return proxy(request, (await params).path);
}
