"use client";

import { useState, type FormEvent } from "react";
import { Button, Card } from "@/components/ui";
import { api, ApiError, type CurrentUser } from "@/lib/api";

const inputCls = "rounded-xl border border-nx-line bg-white/5 px-4 py-3 text-nx-text outline-none focus:border-nx-accent";

export function ChangePasswordForm({ onDone, title = "Cambiar contrasena" }: { onDone?: () => void; title?: string }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next !== repeat) {
      setMsg({ ok: false, text: "Las contrasenas nuevas no coinciden." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.changePassword(current, next);
      setMsg({ ok: true, text: "Contrasena actualizada. Se cerraron tus otras sesiones." });
      setCurrent("");
      setNext("");
      setRepeat("");
      onDone?.();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "No se pudo cambiar." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <h2 className="text-lg font-bold">{title}</h2>
      <input className={inputCls} type="password" required placeholder="Contrasena actual" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      <input className={inputCls} type="password" required placeholder="Nueva contrasena" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      <input className={inputCls} type="password" required placeholder="Repetir nueva contrasena" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
      {msg && <p className={`text-sm font-semibold ${msg.ok ? "text-nx-accent" : "text-red-300"}`}>{msg.text}</p>}
      <Button type="submit" disabled={busy}>Guardar</Button>
    </form>
  );
}

export function MfaPanel({ user, onEnabled }: { user: CurrentUser; onEnabled?: () => void }) {
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [justEnabled, setJustEnabled] = useState(false);
  const enabled = user.mfa_enabled || justEnabled;

  async function start() {
    setMsg(null);
    try {
      setSetup(await api.mfaSetup());
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "No se pudo generar." });
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault();
    try {
      await api.mfaEnable(code);
      setJustEnabled(true);
      setSetup(null);
      setMsg({ ok: true, text: "MFA activado. Desde ahora el ingreso pide un codigo de la app." });
      onEnabled?.();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Codigo incorrecto." });
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-lg font-bold">Verificacion en dos pasos (MFA)</h2>
      {enabled ? (
        <p className="text-sm text-nx-accent">Activa. Cada ingreso pide el codigo de tu app autenticadora.</p>
      ) : !setup ? (
        <>
          <p className="text-sm text-nx-muted">
            Usa una app autenticadora (Google Authenticator, Microsoft Authenticator, Authy...).
            {user.mfa_required && " Es obligatoria para el personal de NEXATEC."}
          </p>
          <Button onClick={start}>Configurar</Button>
        </>
      ) : (
        <form onSubmit={confirm} className="flex flex-col gap-3">
          <ol className="list-decimal pl-5 text-sm text-nx-muted">
            <li>En la app, agregar cuenta con <strong>clave de configuracion</strong> (o abrir el enlace desde el celular).</li>
            <li>Ingresar abajo el codigo de 6 digitos que muestra la app.</li>
          </ol>
          <p className="rounded-xl border border-nx-line bg-black/30 p-3 text-center font-mono text-lg tracking-widest">
            {setup.secret.match(/.{1,4}/g)?.join(" ")}
          </p>
          <a href={setup.otpauth_uri} className="text-center text-sm text-nx-accent underline">Abrir en la app autenticadora</a>
          <input className={inputCls} inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required placeholder="Codigo de 6 digitos"
            value={code} onChange={(e) => setCode(e.target.value)} />
          <Button type="submit">Activar</Button>
        </form>
      )}
      {msg && <p className={`text-sm font-semibold ${msg.ok ? "text-nx-accent" : "text-red-300"}`}>{msg.text}</p>}
    </div>
  );
}

export function SecuritySection({ user, onChange }: { user: CurrentUser; onChange?: () => void }) {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <Card className="p-6"><MfaPanel user={user} onEnabled={onChange} /></Card>
      <Card className="p-6"><ChangePasswordForm /></Card>
    </div>
  );
}
