"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Button, Card, TopNav } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

const inputCls = "rounded-xl border border-nx-line bg-white/5 px-4 py-3 text-nx-text outline-none focus:border-nx-accent";

function Form() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const invitation = params.get("invitacion") === "1";
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== repeat) {
      setError("Las contrasenas no coinciden.");
      return;
    }
    try {
      await api.confirmPasswordReset(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo guardar.");
    }
  }

  if (!token) return <p className="mt-4 text-sm text-red-300">El enlace esta incompleto. Pedi uno nuevo.</p>;
  if (done) {
    return (
      <div className="mt-4 flex flex-col gap-4">
        <p className="text-sm text-nx-accent">Listo. Ya podes ingresar con tu contrasena nueva.</p>
        <Link href="/login" className="text-center font-bold text-nx-accent">Ir a ingresar</Link>
      </div>
    );
  }
  return (
    <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
      <p className="text-sm text-nx-muted">{invitation ? "Elegi la contrasena para activar tu cuenta." : "Elegi tu contrasena nueva."}</p>
      <input className={inputCls} type="password" required autoComplete="new-password" placeholder="Contrasena nueva" value={password} onChange={(e) => setPassword(e.target.value)} />
      <input className={inputCls} type="password" required autoComplete="new-password" placeholder="Repetir contrasena" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
      {error && <p className="text-sm font-semibold text-red-300">{error}</p>}
      <Button type="submit">Guardar</Button>
    </form>
  );
}

export default function RestablecerPage() {
  return (
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/login" />
      <main className="mx-auto flex w-full max-w-md flex-1 items-center px-5 py-16">
        <Card className="w-full p-8">
          <h1 className="text-2xl font-extrabold">Contrasena</h1>
          {/* useSearchParams en una pagina prerenderizada necesita Suspense. */}
          <Suspense fallback={<p className="mt-4 text-nx-muted">Cargando...</p>}><Form /></Suspense>
        </Card>
      </main>
    </div>
  );
}
