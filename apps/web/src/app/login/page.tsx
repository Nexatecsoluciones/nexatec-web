"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ChangePasswordForm } from "@/components/security";
import { Button, Card, TopNav } from "@/components/ui";
import { useTurnstileToken } from "@/components/turnstile";
import { api, ApiError, type LoginStage, type Role } from "@/lib/api";

const ADMIN = ["SUPER_ADMIN", "ADMIN", "SUPPORT", "BILLING"];

const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Pasos posteriores a la contrasena: la sesion queda restringida en el
  // servidor hasta completarlos, no alcanza con saltearlos en el navegador.
  const [stage, setStage] = useState<LoginStage>("FULL");
  const [role, setRole] = useState<Role | null>(null);
  const [code, setCode] = useState("");

  function proceed(next: LoginStage, r: Role | null = role) {
    if (next === "FULL") router.push(r && ADMIN.includes(r) ? "/admin" : "/portal");
    else setStage(next);
  }

  async function onMfa(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      proceed((await api.mfaVerify(code)).next);
    } catch (err) {
      setCode("");
      if (err instanceof ApiError && err.status === 401) {
        setStage("FULL");
        setError("Demasiados codigos incorrectos. Volve a ingresar tu contrasena.");
      } else setError("Codigo incorrecto. Revisa la hora de tu telefono.");
    } finally {
      setLoading(false);
    }
  }
  const { token: turnstileToken, widget: turnstileWidget, reset: resetTurnstile } =
    useTurnstileToken(TURNSTILE_SITE_KEY);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (TURNSTILE_SITE_KEY && !turnstileToken) {
      setError("Completa la verificacion anti-bot antes de continuar.");
      return;
    }

    setLoading(true);
    try {
      // Sin site key configurada (solo puede pasar en development, ver
      // app/security/turnstile.py) no hay widget y se envia vacio: la API
      // decide si eso es aceptable segun el entorno, nunca el cliente.
      const res = await api.login(email, password, turnstileToken);
      setRole(res.role);
      setPassword("");
      proceed(res.next, res.role);
    } catch (err) {
      resetTurnstile();
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
        {stage === "MFA" ? (
          <Card className="w-full p-8">
            <h1 className="text-2xl font-extrabold">Verificacion en dos pasos</h1>
            <p className="mt-1 text-sm text-nx-muted">Ingresa el codigo de 6 digitos de tu app autenticadora.</p>
            <form onSubmit={onMfa} className="mt-6 flex flex-col gap-4">
              <input autoFocus inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required autoComplete="one-time-code"
                value={code} onChange={(e) => setCode(e.target.value)}
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-3 text-center text-2xl tracking-[0.4em] text-nx-text outline-none focus:border-nx-accent" />
              {error && <p className="text-sm font-semibold text-red-300">{error}</p>}
              <Button type="submit" disabled={loading} className="w-full">Verificar</Button>
            </form>
          </Card>
        ) : stage === "PASSWORD_CHANGE" ? (
          <Card className="w-full p-8">
            <p className="mb-4 text-sm text-nx-muted">Por seguridad tenes que elegir una contrasena nueva antes de continuar.</p>
            <ChangePasswordForm title="Elegi una contrasena nueva" onDone={() => proceed("FULL")} />
          </Card>
        ) : (
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

            {turnstileWidget}

            {error && <p className="text-sm font-semibold text-red-300">{error}</p>}

            <Button type="submit" disabled={loading} className="mt-2 w-full">
              {loading ? "Ingresando..." : "Ingresar"}
            </Button>
          </form>
        </Card>
        )}
      </main>
    </div>
  );
}
