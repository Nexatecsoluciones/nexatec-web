"use client";

import { useState, type FormEvent } from "react";
import { useTurnstileToken } from "@/components/turnstile";
import { Button, Card, TopNav } from "@/components/ui";
import { api } from "@/lib/api";

const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";

export default function RecuperarPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { token, widget, reset } = useTurnstileToken(TURNSTILE_SITE_KEY);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (TURNSTILE_SITE_KEY && !token) {
      setError("Completa la verificacion anti-bot.");
      return;
    }
    try {
      await api.requestPasswordReset(email, token);
      // Mismo mensaje exista o no la cuenta (no se revela quien esta registrado).
      setSent(true);
    } catch {
      reset();
      setError("No se pudo procesar el pedido. Intenta de nuevo.");
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/login" />
      <main className="mx-auto flex w-full max-w-md flex-1 items-center px-5 py-16">
        <Card className="w-full p-8">
          <h1 className="text-2xl font-extrabold">Recuperar contrasena</h1>
          {sent ? (
            <p className="mt-4 text-sm text-nx-muted">
              Si existe una cuenta con ese email, te enviamos un enlace para elegir una contrasena nueva. Vence en 30 minutos.
              Si no llega, revisa la carpeta de spam o escribinos por WhatsApp.
            </p>
          ) : (
            <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
              <input type="email" required placeholder="tu@email.com" value={email} onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-3 text-nx-text outline-none focus:border-nx-accent" />
              {widget}
              {error && <p className="text-sm font-semibold text-red-300">{error}</p>}
              <Button type="submit">Enviar enlace</Button>
            </form>
          )}
        </Card>
      </main>
    </div>
  );
}
