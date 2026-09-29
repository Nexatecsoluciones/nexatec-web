"use client";

import { useEffect, useState, type FormEvent } from "react";
import { AdminLayout, Button, Card } from "@/components/ui";
import { useAdminGuard } from "@/lib/useAdminGuard";
import { api, ApiError, type PlanOut } from "@/lib/api";

export default function AdminPlanesPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [plans, setPlans] = useState<PlanOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function loadPlans() {
    setPlans(await api.listPlans());
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user) loadPlans();
  }, [user]);

  async function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      await api.createPlan({
        slug: String(form.get("slug")),
        name: String(form.get("name")),
        price_amount: String(form.get("price_amount")),
        billing_period: String(form.get("billing_period")),
      });
      e.currentTarget.reset();
      await loadPlans();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Error al crear el plan.");
    }
  }

  if (loading) return <div className="flex flex-1 items-center justify-center text-nx-muted">Cargando...</div>;
  if (forbidden || !user) {
    return (
      <div className="flex flex-1 items-center justify-center p-10 text-center">
        <Card className="p-8"><h1 className="text-xl font-bold">No autorizado</h1></Card>
      </div>
    );
  }

  return (
    <AdminLayout activePath="/admin/planes" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <h1 className="text-3xl font-extrabold">Planes</h1>
      <p className="mt-1 text-nx-muted">Precios y periodicidad, sin hardcodear en el frontend.</p>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[1.2fr_0.8fr]">
        <Card className="overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-nx-line text-nx-muted">
              <tr><th className="px-4 py-3">Nombre</th><th className="px-4 py-3">Precio</th><th className="px-4 py-3">Periodo</th></tr>
            </thead>
            <tbody>
              {plans.map((p) => (
                <tr key={p.id} className="border-b border-nx-line/50">
                  <td className="px-4 py-2.5">{p.name}</td>
                  <td className="px-4 py-2.5">{p.price_currency} {Number(p.price_amount).toLocaleString("es-PY")}</td>
                  <td className="px-4 py-2.5">{p.billing_period}</td>
                </tr>
              ))}
              {plans.length === 0 && <tr><td colSpan={3} className="px-4 py-6 text-center text-nx-muted">Sin planes todavía.</td></tr>}
            </tbody>
          </table>
        </Card>

        <Card className="p-6">
          <h2 className="text-lg font-bold">Nuevo plan</h2>
          <form onSubmit={onCreate} className="mt-4 flex flex-col gap-3">
            <input name="slug" placeholder="slug (ej: starter)" required pattern="[a-z0-9-]+"
              className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
            <input name="name" placeholder="Nombre" required
              className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
            <input name="price_amount" placeholder="Precio (PYG)" required type="number" step="0.01"
              className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
            <select name="billing_period" className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm">
              <option value="MONTHLY">Mensual</option>
              <option value="ANNUAL">Anual</option>
            </select>
            {error && <p className="text-sm font-semibold text-red-300">{error}</p>}
            <Button type="submit" className="w-full">Crear plan</Button>
          </form>
        </Card>
      </div>
    </AdminLayout>
  );
}
