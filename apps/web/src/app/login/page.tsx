"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button, Card, TopNav } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      // TODO(FASE 7): reemplazar "dev" por el token real del widget de
      // Turnstile una vez configurado NEXT_PUBLIC_TURNSTILE_SITE_KEY.
      const turnstileToken = TURNSTILE_SITE_KEY ? "" : "dev";
      const { role } = await api.login(email, password, turnstileToken);
      const adminRoles = ["SUPER_ADMIN", "ADMIN", "SUPPORT", "BILLING"];
      router.push(adminRoles.includes(role) ? "/admin" : "/portal");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          err.status === 429
            ? "Demasiados intentos fallidos. Intenta de nuevo mas tarde."
            : "Email o contrasena incorrectos.",
        );
      } else {
        setError("No se pudo conectar con el servidor.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/login" />
      <main className="mx-auto flex w-full max-w-md flex-1 items-center px-5 py-16">
        <Card className="w-full p-8">
          <h1 className="text-2xl font-extrabold">Ingresar</h1>
          <p className="mt-1 text-sm text-nx-muted">Portal de clientes NEXATEC.</p>

          <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4">
            <label className="flex flex-col gap-1 text-sm font-semibold">
              Email
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-3 text-nx-text outline-none focus:border-nx-accent"
                autoComplete="username"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-semibold">
              Contrasena
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-3 text-nx-text outline-none focus:border-nx-accent"
                autoComplete="current-password"
              />
            </label>

            {error && <p className="text-sm font-semibold text-red-300">{error}</p>}

            <Button type="submit" disabled={loading} className="mt-2 w-full">
              {loading ? "Ingresando..." : "Ingresar"}
            </Button>
          </form>
        </Card>
      </main>
    </div>
  );
}
