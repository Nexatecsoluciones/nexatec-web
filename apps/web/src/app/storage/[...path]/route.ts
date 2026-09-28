import { NextRequest, NextResponse } from "next/server";

/**
 * BFF same-origin para archivos: el navegador solo conoce
 * https://staging.nexatecpy.com/storage/<bucket>/<key>?<firma>. Reenvia
 * server-side hacia Garage (S3 interno). Nunca se toca la query string
 * (contiene la firma SigV4) ni se agregan/quitan parametros -- Garage
 * necesita ver exactamente lo mismo que la API firmo (ver
 * app/services/storage.py::generate_presigned_get_url).
 *
 * Solo GET: las URLs firmadas que emite la API son siempre de lectura.
 * No es un proxy abierto: el destino es siempre
 * NEXATEC_INTERNAL_S3_URL/<path capturado>, nunca un host arbitrario.
 */

const INTERNAL_S3_URL = process.env.NEXATEC_INTERNAL_S3_URL ?? "http://127.0.0.1:3900";

type RouteParams = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { path } = await params;
  const targetUrl = `${INTERNAL_S3_URL}/${path.join("/")}${request.nextUrl.search}`;

  const upstream = await fetch(targetUrl, { method: "GET", cache: "no-store" });

  const headers = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!["transfer-encoding", "connection"].includes(key.toLowerCase())) headers.set(key, value);
  });

  return new NextResponse(upstream.body, { status: upstream.status, headers });
}
