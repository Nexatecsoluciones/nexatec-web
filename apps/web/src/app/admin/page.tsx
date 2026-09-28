"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Button, Card, TopNav } from "@/components/ui";
import { ADMIN_ROLES, api, ApiError, type CurrentUser, type SystemOut } from "@/lib/api";

export default function AdminPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [systems, setSystems] = useState<SystemOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  async function loadSystems() {
    // La lista completa (incluye inactivos) solo la devuelve la API si el
    // rol es admin: el backend decide, esto es solo para pintar la UI.
    const data = await api.listAllSystemsAdmin();
    setSystems(data);
  }

  useEffect(() => {
    api
      .me()
      .then(async (me) => {
        setUser(me);
        if (!ADMIN_ROLES.includes(me.role)) {
          setForbidden(true);
          return;
        }
        await loadSystems();
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
        } else if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
        }
      })
      .finally(() => setLoading(false));
  }, [router]);

  async function onCreateSystem(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFormError(null);
    setCreating(true);
    const form = new FormData(e.currentTarget);
    try {
      await api.createSystemAdmin({
        slug: String(form.get("slug")),
        name: String(form.get("name")),
        short_description: String(form.get("short_description")),
        category: String(form.get("category")),
      });
      e.currentTarget.reset();
      await loadSystems();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Error al crear el sistema.");
    } finally {
      setCreating(false);
    }
  }

  if (loading) {
    return (
      <div className="flex flex-1 flex-col">
        <TopNav activePath="/admin" />
        <main className="flex-1 px-5 py-16 text-center text-nx-muted">Cargando...</main>
      </div>
    );
  }

  if (forbidden || !user) {
    return (
      <div className="flex flex-1 flex-col">
        <TopNav activePath="/admin" />
        <main className="mx-auto w-full max-w-lg flex-1 px-5 py-16 text-center">
          <Card className="p-8">
            <h1 className="text-xl font-bold">No autorizado</h1>
            <p className="mt-2 text-nx-muted">
              Tu cuenta no tiene permisos para acceder al panel de administracion.
            </p>
          </Card>
        </main>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/admin" />
      <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-16">
        <h1 className="text-3xl font-extrabold">Panel de administracion</h1>
        <p className="mt-1 text-nx-muted">{user.email} — {user.role}</p>

        <div className="mt-10 grid grid-cols-1 gap-8 lg:grid-cols-[1.2fr_0.8fr]">
          <Card className="overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-nx-line text-nx-muted">
                <tr>
                  <th className="px-5 py-4">Nombre</th>
                  <th className="px-5 py-4">Categoria</th>
                  <th className="px-5 py-4">Activo</th>
                </tr>
              </thead>
              <tbody>
                {systems.map((s) => (
                  <tr key={s.id} className="border-b border-nx-line/50">
                    <td className="px-5 py-3 font-semibold">{s.name}</td>
                    <td className="px-5 py-3 text-nx-muted">{s.category}</td>
                    <td className="px-5 py-3">{s.is_active ? "Si" : "No"}</td>
                  </tr>
                ))}
                {systems.length === 0 && (
                  <tr>
                    <td colSpan={3} className="px-5 py-6 text-center text-nx-muted">
                      Sin sistemas cargados todavia.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </Card>

          <Card className="p-6">
            <h2 className="text-lg font-bold">Agregar sistema</h2>
            <form onSubmit={onCreateSystem} className="mt-4 flex flex-col gap-3">
              <input
                name="slug"
                placeholder="slug (ej: taller)"
                required
                pattern="[a-z0-9-]+"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent"
              />
              <input
                name="name"
                placeholder="Nombre"
                required
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent"
              />
              <input
                name="category"
                placeholder="Categoria"
                required
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent"
              />
              <textarea
                name="short_description"
                placeholder="Descripcion corta"
                required
                rows={3}
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent"
              />
              {formError && <p className="text-sm font-semibold text-red-300">{formError}</p>}
              <Button type="submit" disabled={creating} className="w-full">
                {creating ? "Creando..." : "Crear sistema"}
              </Button>
            </form>
          </Card>
        </div>
      </main>
    </div>
  );
}
