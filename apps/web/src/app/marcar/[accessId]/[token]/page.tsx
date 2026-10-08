"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";

// Marcacion de asistencia en el celular/tablet de la obra (sin login).
// El token del link viaja en un encabezado; el aparato se identifica con un
// id propio guardado en localStorage (el link queda atado al primer aparato).
// Sin señal: la marca se guarda con su hora real y se reenvia sola despues
// (el servidor la acepta hasta 24 h despues y no la duplica: client_id).

const FP_KEY = "nx-marcacion-fp";
const QUEUE_KEY = "nx-marcacion-cola";

type Pending = { national_id: string; client_id: string; occurred_at: string; latitude?: number; longitude?: number };
type Info = { company: string | null; site: string; requires_location: boolean };
type Result = { ok: boolean; title: string; detail?: string };

function store<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: unknown) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* sin storage */ }
}

function fingerprint(): string {
  let fp = store<string>(FP_KEY, "");
  if (!fp) {
    fp = `fp-${crypto.randomUUID()}`;
    save(FP_KEY, fp);
  }
  return fp;
}

function position(): Promise<GeolocationPosition | null> {
  return new Promise((resolve) => {
    if (!("geolocation" in navigator)) return resolve(null);
    navigator.geolocation.getCurrentPosition(resolve, () => resolve(null), { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
  });
}

export default function MarcarPage() {
  const { accessId, token } = useParams<{ accessId: string; token: string }>();
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ci, setCi] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [queue, setQueue] = useState<Pending[]>([]);
  const [clock, setClock] = useState("");

  const call = useCallback(async (path: string, body?: unknown) => fetch(`/api/public/hr/${accessId}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Device-Token": token, "X-Device-Fp": fingerprint() },
    body: JSON.stringify(body ?? {}),
  }), [accessId, token]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQueue(store<Pending[]>(QUEUE_KEY, []));
    call("/device").then(async (r) => {
      if (r.ok) setInfo(await r.json());
      else setError((await r.json().catch(() => ({}))).detail ?? "Link de marcacion invalido.");
    }).catch(() => setError("Sin conexion. Reintenta cuando haya señal."));
    const tick = () => setClock(new Date().toLocaleTimeString("es-PY", { hour: "2-digit", minute: "2-digit", timeZone: "America/Asuncion" }));
    tick();
    const id = setInterval(tick, 10000);
    return () => clearInterval(id);
  }, [call]);

  const flush = useCallback(async () => {
    const pending = store<Pending[]>(QUEUE_KEY, []);
    const left: Pending[] = [];
    for (const p of pending) {
      try {
        const r = await call("/mark", p);
        if (r.status >= 500) left.push(p); // el servidor no pudo: reintentar despues
      } catch {
        left.push(p); // sigue sin señal
      }
    }
    save(QUEUE_KEY, left);
    setQueue(left);
  }, [call]);

  useEffect(() => {
    window.addEventListener("online", flush);
    const id = setInterval(flush, 60000);
    return () => { window.removeEventListener("online", flush); clearInterval(id); };
  }, [flush]);

  async function mark() {
    if (ci.length < 5 || busy) return;
    setBusy(true);
    setResult(null);
    const pos = info?.requires_location ? await position() : null;
    if (info?.requires_location && !pos) {
      setResult({ ok: false, title: "Necesitamos la ubicacion", detail: "Activa la ubicacion del celular y permitila para esta pagina." });
      setBusy(false);
      return;
    }
    const item: Pending = { national_id: ci, client_id: crypto.randomUUID(), occurred_at: new Date().toISOString(),
      ...(pos ? { latitude: pos.coords.latitude, longitude: pos.coords.longitude } : {}) };
    try {
      const r = await call("/mark", item);
      const body = await r.json().catch(() => ({}));
      if (r.ok) {
        const what = body.result === "OUT" ? "Salida" : "Entrada";
        setResult({ ok: true, title: `${what} registrada ${body.time}`, detail: body.employee });
      } else {
        setResult({ ok: false, title: "No se pudo marcar", detail: body.detail ?? "Intenta de nuevo." });
      }
    } catch {
      const q = [...store<Pending[]>(QUEUE_KEY, []), item];
      save(QUEUE_KEY, q);
      setQueue(q);
      setResult({ ok: true, title: "Guardada sin señal", detail: "Se envia sola cuando vuelva la conexion, con la hora de ahora." });
    }
    setCi("");
    setBusy(false);
  }

  const key = (k: string) => {
    if (k === "<") setCi((v) => v.slice(0, -1));
    else if (k === "C") setCi("");
    else setCi((v) => (v.length < 12 ? v + k : v));
  };

  if (error) {
    return <main className="grid min-h-screen place-items-center p-6 text-center"><div><p className="text-2xl font-extrabold">Marcacion</p><p className="mt-3 text-red-300">{error}</p></div></main>;
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-md flex-col gap-4 p-5">
      <header className="text-center">
        <p className="text-xs uppercase tracking-widest text-nx-muted">{info?.company ?? "NEXATEC"}</p>
        <h1 className="text-2xl font-extrabold">{info?.site ?? "Cargando..."}</h1>
        <p className="text-5xl font-extrabold tabular-nums">{clock}</p>
      </header>

      <div className="rounded-2xl border border-nx-line bg-black/30 p-4 text-center">
        <p className="text-xs text-nx-muted">Numero de cedula</p>
        <p className="min-h-[44px] text-4xl font-extrabold tracking-widest tabular-nums">{ci || " "}</p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "<"].map((k) => (
          <button key={k} onClick={() => key(k)} disabled={!info || busy}
            className="min-h-[64px] rounded-2xl border border-nx-line bg-white/5 text-2xl font-bold active:bg-nx-accent/30 disabled:opacity-50">
            {k === "<" ? (
              <svg viewBox="0 0 24 24" aria-label="Borrar un digito" className="mx-auto h-7 w-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 5H9l-6 7 6 7h12a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1z" /><path d="M17 9l-6 6M11 9l6 6" />
              </svg>
            ) : k === "C" ? "Borrar" : k}
          </button>
        ))}
      </div>

      <button onClick={mark} disabled={!info || busy || ci.length < 5}
        className="min-h-[64px] rounded-2xl bg-nx-accent text-xl font-extrabold text-[#06352f] disabled:opacity-50">
        {busy ? "Marcando..." : "Marcar entrada / salida"}
      </button>

      {result && (
        <div role="status" className={`rounded-2xl p-4 text-center ${result.ok ? "bg-nx-accent/15 text-nx-accent" : "bg-red-500/15 text-red-200"}`}>
          <p className="text-xl font-extrabold">{result.title}</p>
          {result.detail && <p className="mt-1 text-sm">{result.detail}</p>}
        </div>
      )}
      {queue.length > 0 && <p className="text-center text-xs text-amber-300">{queue.length} marca(s) esperando señal para enviarse.</p>}
      <p className="mt-auto text-center text-[11px] text-nx-muted">La primera marca del dia es la entrada; la segunda, la salida.</p>
    </main>
  );
}
