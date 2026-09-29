"use client";

import { useEffect, useState, type FormEvent } from "react";
import { AdminLayout, Badge, Button, Card } from "@/components/ui";
import { useAdminGuard } from "@/lib/useAdminGuard";
import {
  api,
  ApiError,
  type ConfigFieldDef,
  type ModuleDef,
  type SystemOut,
} from "@/lib/api";

const FIELD_TYPES: ConfigFieldDef["type"][] = ["text", "select", "boolean", "number", "media", "secret"];

const IMPLEMENTATION_LABEL: Record<string, string> = {
  PENDING: "Implementación: PENDIENTE",
  PARTIAL: "Implementación: PARCIAL",
  READY: "Implementación: LISTA",
};

function emptyField(): ConfigFieldDef {
  return { key: "", label: "", type: "text", required: false };
}

function emptyModule(): ModuleDef {
  return { key: "", label: "", default_enabled: false };
}

export default function ProductStudioPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [products, setProducts] = useState<SystemOut[]>([]);
  const [selected, setSelected] = useState<SystemOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState<string | null>(null);

  const [fields, setFields] = useState<ConfigFieldDef[]>([]);
  const [modules, setModules] = useState<ModuleDef[]>([]);

  async function loadProducts() {
    const data = await api.listAllSystemsAdmin();
    setProducts(data);
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user) loadProducts();
  }, [user]);

  function startCreate() {
    setSelected(null);
    setCreating(true);
    setFields([]);
    setModules([]);
    setSaveError(null);
    setSaveOk(null);
  }

  function openProduct(p: SystemOut) {
    setSelected(p);
    setCreating(false);
    setFields(p.config_schema ?? []);
    setModules(p.modules_schema ?? []);
    setSaveError(null);
    setSaveOk(null);
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaveError(null);
    setSaveOk(null);
    const form = new FormData(e.currentTarget);

    const cleanFields = fields.filter((f) => f.key.trim() && f.label.trim());
    const cleanModules = modules.filter((m) => m.key.trim() && m.label.trim());

    const payload = {
      name: String(form.get("name")),
      short_description: String(form.get("short_description")),
      category: String(form.get("category")),
      description: String(form.get("description") || "") || null,
      demo_available: form.get("demo_available") === "on",
      production_available: form.get("production_available") === "on",
      is_active: form.get("is_active") === "on",
      is_public: form.get("is_public") === "on",
      implementation_status: String(form.get("implementation_status")) as SystemOut["implementation_status"],
      default_demo_duration_days: Number(form.get("default_demo_duration_days") || 14),
      config_schema: cleanFields,
      modules_schema: cleanModules,
    };

    try {
      if (creating) {
        const slug = String(form.get("slug"));
        const created = await api.createSystemAdmin({ slug, ...payload });
        await loadProducts();
        openProduct(created);
      } else if (selected) {
        const updated = await api.updateSystemAdmin(selected.id, payload);
        await loadProducts();
        openProduct(updated);
      }
      setSaveOk("Guardado correctamente.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Error al guardar.");
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

  const showForm = creating || selected;

  return (
    <AdminLayout activePath="/admin/productos" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-extrabold">Product Studio</h1>
          <p className="mt-1 text-nx-muted">
            Define productos y su configuración no-code. Esto crea la DEFINICIÓN del producto,
            no el software detrás.
          </p>
        </div>
        <Button onClick={startCreate}>+ Nuevo producto</Button>
      </div>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[0.7fr_1.3fr]">
        <Card className="overflow-hidden">
          <ul>
            {products.map((p) => (
              <li key={p.id}>
                <button
                  onClick={() => openProduct(p)}
                  className={`w-full px-5 py-3 text-left text-sm hover:bg-white/5 ${selected?.id === p.id ? "bg-white/10" : ""}`}
                >
                  <span className="font-semibold">{p.name}</span>
                  <span className="ml-2 text-nx-muted">{p.category}</span>
                  <br />
                  <span className="text-xs text-nx-muted">{IMPLEMENTATION_LABEL[p.implementation_status]}</span>
                </button>
              </li>
            ))}
            {products.length === 0 && <li className="px-5 py-6 text-center text-sm text-nx-muted">Sin productos todavía.</li>}
          </ul>
        </Card>

        {!showForm ? (
          <Card className="p-8 text-nx-muted">Elegí un producto o creá uno nuevo.</Card>
        ) : (
          <form onSubmit={onSubmit}>
            <Card className="p-6">
              <h2 className="text-lg font-bold">{creating ? "Nuevo producto" : selected!.name}</h2>
              {!creating && <Badge>{IMPLEMENTATION_LABEL[selected!.implementation_status]}</Badge>}

              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                {creating && (
                  <input name="slug" placeholder="slug (ej: crm-empresas)" required pattern="[a-z0-9-]+"
                    className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent sm:col-span-2" />
                )}
                <input name="name" placeholder="Nombre" required defaultValue={selected?.name}
                  className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
                <input name="category" placeholder="Categoría" required defaultValue={selected?.category}
                  className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
                <textarea name="short_description" placeholder="Descripción corta" required rows={2}
                  defaultValue={selected?.short_description}
                  className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent sm:col-span-2" />
                <textarea name="description" placeholder="Descripción comercial (opcional)" rows={3}
                  defaultValue={selected?.description ?? ""}
                  className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent sm:col-span-2" />

                <select name="implementation_status" defaultValue={selected?.implementation_status ?? "PENDING"}
                  className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm">
                  <option value="PENDING">Implementación: PENDIENTE</option>
                  <option value="PARTIAL">Implementación: PARCIAL</option>
                  <option value="READY">Implementación: LISTA</option>
                </select>
                <input name="default_demo_duration_days" type="number" min={1} max={365}
                  defaultValue={selected?.default_demo_duration_days ?? 14}
                  placeholder="Duración demo default (días)"
                  className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />

                <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="is_active" defaultChecked={selected?.is_active ?? true} /> Activo</label>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="is_public" defaultChecked={selected?.is_public ?? false} /> Público (catálogo)</label>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="demo_available" defaultChecked={selected?.demo_available ?? false} /> Demo disponible</label>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="production_available" defaultChecked={selected?.production_available ?? true} /> Producción disponible</label>
              </div>

              <h3 className="mt-8 text-sm font-bold uppercase tracking-wide text-nx-muted">
                Configuración (no-code)
              </h3>
              <p className="text-xs text-nx-muted">Campos que va a poder editar el admin desde Configuration Center por instancia.</p>
              <div className="mt-3 flex flex-col gap-2">
                {fields.map((f, i) => (
                  <div key={i} className="grid grid-cols-[1fr_1fr_0.7fr_auto] gap-2">
                    <input placeholder="key" value={f.key}
                      onChange={(e) => setFields(fields.map((x, j) => j === i ? { ...x, key: e.target.value } : x))}
                      className="rounded-lg border border-nx-line bg-white/5 px-3 py-2 text-xs" />
                    <input placeholder="Label" value={f.label}
                      onChange={(e) => setFields(fields.map((x, j) => j === i ? { ...x, label: e.target.value } : x))}
                      className="rounded-lg border border-nx-line bg-white/5 px-3 py-2 text-xs" />
                    <select value={f.type}
                      onChange={(e) => setFields(fields.map((x, j) => j === i ? { ...x, type: e.target.value as ConfigFieldDef["type"] } : x))}
                      className="rounded-lg border border-nx-line bg-white/5 px-2 py-2 text-xs">
                      {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <button type="button" onClick={() => setFields(fields.filter((_, j) => j !== i))}
                      className="rounded-lg border border-red-400/30 px-2 text-xs text-red-300">×</button>
                  </div>
                ))}
                <Button type="button" variant="secondary" className="min-h-[36px] self-start px-4 text-xs"
                  onClick={() => setFields([...fields, emptyField()])}>+ Agregar campo</Button>
              </div>

              <h3 className="mt-8 text-sm font-bold uppercase tracking-wide text-nx-muted">Módulos</h3>
              <div className="mt-3 flex flex-col gap-2">
                {modules.map((m, i) => (
                  <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                    <input placeholder="key" value={m.key}
                      onChange={(e) => setModules(modules.map((x, j) => j === i ? { ...x, key: e.target.value } : x))}
                      className="rounded-lg border border-nx-line bg-white/5 px-3 py-2 text-xs" />
                    <input placeholder="Label" value={m.label}
                      onChange={(e) => setModules(modules.map((x, j) => j === i ? { ...x, label: e.target.value } : x))}
                      className="rounded-lg border border-nx-line bg-white/5 px-3 py-2 text-xs" />
                    <button type="button" onClick={() => setModules(modules.filter((_, j) => j !== i))}
                      className="rounded-lg border border-red-400/30 px-2 text-xs text-red-300">×</button>
                  </div>
                ))}
                <Button type="button" variant="secondary" className="min-h-[36px] self-start px-4 text-xs"
                  onClick={() => setModules([...modules, emptyModule()])}>+ Agregar módulo</Button>
              </div>

              {saveError && <p className="mt-4 text-sm font-semibold text-red-300">{saveError}</p>}
              {saveOk && <p className="mt-4 text-sm font-semibold text-nx-accent">{saveOk}</p>}
              <Button type="submit" className="mt-6 w-full">{creating ? "Crear producto" : "Guardar cambios"}</Button>
            </Card>
          </form>
        )}
      </div>
    </AdminLayout>
  );
}
