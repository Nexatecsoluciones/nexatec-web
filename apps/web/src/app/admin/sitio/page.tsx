"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Announcement, HomeBody } from "@/components/home";
import { AdminLayout, Button, Card } from "@/components/ui";
import { api, ApiError, type SitePageOut, type SiteVersionOut } from "@/lib/api";
import type { Card as CardItem, Faq, HomeContent, Plan } from "@/lib/site-content";
import { useAdminGuard } from "@/lib/useAdminGuard";

const inputCls = "w-full rounded-xl border border-nx-line bg-white/5 px-3 py-2 text-sm text-nx-text outline-none focus:border-nx-accent disabled:opacity-60";

/** Las listas "una por linea" pueden tener lineas vacias mientras se tipea. */
function normalize(c: HomeContent): HomeContent {
  const lines = (xs: string[]) => xs.map((x) => x.trim()).filter(Boolean);
  return { ...c, sectors: lines(c.sectors), plans: c.plans.map((p) => ({ ...p, items: lines(p.items) })) };
}

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString("es-PY", { timeZone: "America/Asuncion", dateStyle: "short", timeStyle: "short" }) : "-";
}

function TextField({ label, value, onChange, max, area, disabled, hint }: {
  label: string; value: string; onChange: (v: string) => void; max: number; area?: boolean; disabled?: boolean; hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="flex justify-between text-xs font-semibold text-nx-muted">
        <span>{label}</span><span className={value.length > max ? "text-red-300" : ""}>{value.length}/{max}</span>
      </span>
      {area
        ? <textarea className={`${inputCls} min-h-[84px]`} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
        : <input className={inputCls} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />}
      {hint && <span className="text-xs text-nx-muted">{hint}</span>}
    </label>
  );
}

function Section({ title, children, open = false }: { title: string; children: ReactNode; open?: boolean }) {
  return (
    <details open={open} className="rounded-2xl border border-nx-line bg-black/10 p-4">
      <summary className="cursor-pointer text-base font-bold">{title}</summary>
      <div className="mt-4 flex flex-col gap-3">{children}</div>
    </details>
  );
}

/** Editor generico de listas: mover, quitar, agregar. */
function ListEditor<T>({ items, onChange, make, render, max, disabled, addLabel }: {
  items: T[]; onChange: (items: T[]) => void; make: () => T; render: (item: T, set: (v: T) => void) => ReactNode;
  max: number; disabled?: boolean; addLabel: string;
}) {
  const move = (i: number, d: number) => {
    const next = [...items];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };
  const btn = "rounded-lg border border-nx-line px-2 py-1 text-xs disabled:opacity-40";
  return (
    <div className="flex flex-col gap-3">
      {items.map((item, i) => (
        <div key={i} className="rounded-xl border border-nx-line p-3">
          {render(item, (v) => onChange(items.map((x, j) => (j === i ? v : x))))}
          {!disabled && (
            <div className="mt-2 flex justify-end gap-2">
              <button type="button" className={btn} disabled={i === 0} onClick={() => move(i, -1)}>Subir</button>
              <button type="button" className={btn} disabled={i === items.length - 1} onClick={() => move(i, 1)}>Bajar</button>
              <button type="button" className={`${btn} text-red-300`} onClick={() => onChange(items.filter((_, j) => j !== i))}>Quitar</button>
            </div>
          )}
        </div>
      ))}
      {!disabled && items.length < max && (
        <button type="button" className="self-start rounded-full border border-nx-accent/40 px-4 py-2 text-sm font-bold text-nx-accent" onClick={() => onChange([...items, make()])}>
          + {addLabel}
        </button>
      )}
    </div>
  );
}

export default function AdminSitioPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [page, setPage] = useState<SitePageOut | null>(null);
  const [draft, setDraft] = useState<HomeContent | null>(null);
  const [versions, setVersions] = useState<SiteVersionOut[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [preview, setPreview] = useState(false);

  function apply(p: SitePageOut) {
    setPage(p);
    setDraft(p.draft);
    setDirty(false);
  }

  async function load() {
    const [p, v] = await Promise.all([api.getSitePage("home"), api.listSiteVersions("home")]);
    apply(p);
    setVersions(v);
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user) load().catch((e) => setMsg({ ok: false, text: e instanceof ApiError ? e.message : "No se pudo cargar." }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  // Aviso del navegador si se cierra la pestana con cambios sin guardar.
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  async function run(fn: () => Promise<SitePageOut>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      apply(await fn());
      setVersions(await api.listSiteVersions("home"));
      setMsg({ ok: true, text: ok });
      return true;
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error inesperado." });
      return false;
    } finally {
      setBusy(false);
    }
  }

  const set = (patch: Partial<HomeContent>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setDirty(true);
  };

  if (loading) return <div className="flex flex-1 items-center justify-center text-nx-muted">Cargando...</div>;
  if (forbidden || !user) {
    return <div className="flex flex-1 items-center justify-center p-10"><Card className="p-8"><h1 className="text-xl font-bold">No autorizado</h1></Card></div>;
  }

  const ro = !page?.can_edit;
  const d = draft;

  return (
    <AdminLayout activePath="/admin/sitio" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-extrabold">Sitio web</h1>
          <p className="mt-1 text-nx-muted">Textos de la pagina de inicio. Los cambios se guardan como borrador y se ven en el sitio recien al publicar.</p>
        </div>
        <a href="/" target="_blank" rel="noopener noreferrer" className="text-sm font-bold text-nx-accent hover:underline">Ver sitio publicado ↗</a>
      </div>

      {page && (
        <div className="mt-6 flex flex-wrap items-center gap-3 rounded-2xl border border-nx-line bg-black/20 p-4 text-sm">
          <span>Publicado: <strong>version {page.published_version}</strong> · {when(page.published_at)}</span>
          <span className={dirty || page.has_unpublished_changes ? "font-bold text-amber-300" : "text-nx-muted"}>
            {dirty ? "Cambios sin guardar" : page.has_unpublished_changes ? "Borrador guardado sin publicar" : "Borrador igual al publicado"}
          </span>
          <div className="ml-auto flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setPreview(!preview)}>{preview ? "Volver a editar" : "Vista previa"}</Button>
            {!ro && (
              <>
                <Button variant="secondary" disabled={busy || !dirty} onClick={() => d && run(() => api.saveSiteDraft("home", normalize(d)), "Borrador guardado.")}>Guardar borrador</Button>
                <Button disabled={busy || dirty || !page.has_unpublished_changes}
                  onClick={() => window.confirm("¿Publicar el borrador? Los visitantes lo van a ver en menos de un minuto.") && run(() => api.publishSite("home"), "Publicado.")}>
                  Publicar
                </Button>
                <Button variant="outline" disabled={busy || (!dirty && !page.has_unpublished_changes)}
                  onClick={() => window.confirm("¿Descartar el borrador y volver a lo publicado?") && run(() => api.discardSiteDraft("home"), "Borrador descartado.")}>
                  Descartar
                </Button>
              </>
            )}
          </div>
          {dirty && !ro && <p className="w-full text-xs text-nx-muted">Guarda el borrador antes de publicar.</p>}
          {ro && <p className="w-full text-xs text-nx-muted">Tu rol puede ver pero no editar el sitio.</p>}
        </div>
      )}
      {msg && <p className={`mt-3 text-sm font-semibold ${msg.ok ? "text-nx-accent" : "text-red-300"}`}>{msg.text}</p>}

      {d && preview && (
        <div className="mt-6 overflow-hidden rounded-2xl border-2 border-dashed border-nx-accent/50">
          <p className="bg-nx-accent/10 px-4 py-2 text-xs font-bold uppercase tracking-widest text-nx-accent">Vista previa del borrador (no publicada)</p>
          <Announcement text={d.announcement} />
          <HomeBody c={d} />
        </div>
      )}

      {d && !preview && (
        <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,320px)]">
          <div className="flex flex-col gap-4">
            <Section title="Aviso superior y portada" open>
              <TextField label="Aviso (franja arriba de todo; vacio = no se muestra)" max={160} value={d.announcement} disabled={ro} onChange={(v) => set({ announcement: v })} />
              <TextField label="Antetitulo" max={60} value={d.hero.eyebrow} disabled={ro} onChange={(v) => set({ hero: { ...d.hero, eyebrow: v } })} />
              <TextField label="Titulo principal" max={120} value={d.hero.title} disabled={ro} onChange={(v) => set({ hero: { ...d.hero, title: v } })} />
              <TextField label="Bajada" area max={300} value={d.hero.subtitle} disabled={ro} onChange={(v) => set({ hero: { ...d.hero, subtitle: v } })} />
              <TextField label="Nota bajo los botones" max={120} value={d.hero.note} disabled={ro} onChange={(v) => set({ hero: { ...d.hero, note: v } })} />
              <TextField label="Mensaje del boton de WhatsApp" area max={300} value={d.hero.whatsapp_message} disabled={ro} onChange={(v) => set({ hero: { ...d.hero, whatsapp_message: v } })} />
            </Section>

            <Section title="Soluciones">
              <TextField label="Titulo" max={60} value={d.services_title} disabled={ro} onChange={(v) => set({ services_title: v })} />
              <TextField label="Bajada" area max={300} value={d.services_subtitle} disabled={ro} onChange={(v) => set({ services_subtitle: v })} />
              <ListEditor<CardItem> items={d.services} max={8} disabled={ro} addLabel="Agregar solucion" make={() => ({ title: "", text: "" })}
                onChange={(services) => set({ services })}
                render={(it, upd) => (
                  <div className="grid gap-2">
                    <TextField label="Nombre" max={60} value={it.title} disabled={ro} onChange={(v) => upd({ ...it, title: v })} />
                    <TextField label="Descripcion" area max={300} value={it.text} disabled={ro} onChange={(v) => upd({ ...it, text: v })} />
                  </div>
                )} />
            </Section>

            <Section title="Que resuelve el ERP (modulos)">
              <TextField label="Titulo" max={60} value={d.modules_title} disabled={ro} onChange={(v) => set({ modules_title: v })} />
              <ListEditor<CardItem> items={d.modules} max={12} disabled={ro} addLabel="Agregar modulo" make={() => ({ title: "", text: "" })}
                onChange={(modules) => set({ modules })}
                render={(it, upd) => (
                  <div className="grid gap-2">
                    <TextField label="Modulo" max={60} value={it.title} disabled={ro} onChange={(v) => upd({ ...it, title: v })} />
                    <TextField label="Descripcion" area max={300} value={it.text} disabled={ro} onChange={(v) => upd({ ...it, text: v })} />
                  </div>
                )} />
            </Section>

            <Section title="Planes">
              <TextField label="Titulo" max={60} value={d.plans_title} disabled={ro} onChange={(v) => set({ plans_title: v })} />
              <TextField label="Bajada" area max={300} value={d.plans_subtitle} disabled={ro} onChange={(v) => set({ plans_subtitle: v })} />
              <ListEditor<Plan> items={d.plans} max={4} disabled={ro} addLabel="Agregar plan"
                make={() => ({ tag: "", name: "", price: "", sub: "", text: "", items: [], whatsapp_message: "Hola NEXATEC, quiero informacion de este plan.", featured: false })}
                onChange={(plans) => set({ plans })}
                render={(it, upd) => (
                  <div className="grid gap-2 md:grid-cols-2">
                    <TextField label="Nombre" max={60} value={it.name} disabled={ro} onChange={(v) => upd({ ...it, name: v })} />
                    <TextField label="Etiqueta" max={40} value={it.tag} disabled={ro} onChange={(v) => upd({ ...it, tag: v })} />
                    <TextField label="Precio" max={40} value={it.price} disabled={ro} onChange={(v) => upd({ ...it, price: v })} />
                    <TextField label="Debajo del precio" max={80} value={it.sub} disabled={ro} onChange={(v) => upd({ ...it, sub: v })} />
                    <div className="md:col-span-2"><TextField label="Descripcion" area max={300} value={it.text} disabled={ro} onChange={(v) => upd({ ...it, text: v })} /></div>
                    <div className="md:col-span-2">
                      <TextField label="Incluye (un item por linea, hasta 8)" area max={700} value={it.items.join("\n")} disabled={ro}
                        onChange={(v) => upd({ ...it, items: v.split("\n").map((x) => x.trimStart()).slice(0, 8) })} />
                    </div>
                    <div className="md:col-span-2"><TextField label="Mensaje de WhatsApp" area max={300} value={it.whatsapp_message} disabled={ro} onChange={(v) => upd({ ...it, whatsapp_message: v })} /></div>
                    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={it.featured} disabled={ro} onChange={(e) => upd({ ...it, featured: e.target.checked })} /> Destacado</label>
                  </div>
                )} />
            </Section>

            <Section title="Rubros">
              <TextField label="Titulo" max={60} value={d.sectors_title} disabled={ro} onChange={(v) => set({ sectors_title: v })} />
              <TextField label="Rubros (uno por linea, hasta 20)" area max={1300} value={d.sectors.join("\n")} disabled={ro}
                onChange={(v) => set({ sectors: v.split("\n").map((x) => x.trimStart()).slice(0, 20) })} />
            </Section>

            <Section title="Quienes somos">
              <TextField label="Titulo" max={60} value={d.about_title} disabled={ro} onChange={(v) => set({ about_title: v })} />
              <TextField label="Texto (vacio = no se muestra)" area max={1500} value={d.about} disabled={ro} onChange={(v) => set({ about: v })} />
            </Section>

            <Section title="Preguntas frecuentes">
              <TextField label="Titulo" max={60} value={d.faq_title} disabled={ro} onChange={(v) => set({ faq_title: v })} />
              <ListEditor<Faq> items={d.faqs} max={20} disabled={ro} addLabel="Agregar pregunta" make={() => ({ question: "", answer: "" })}
                onChange={(faqs) => set({ faqs })}
                render={(it, upd) => (
                  <div className="grid gap-2">
                    <TextField label="Pregunta" max={160} value={it.question} disabled={ro} onChange={(v) => upd({ ...it, question: v })} />
                    <TextField label="Respuesta" area max={1000} value={it.answer} disabled={ro} onChange={(v) => upd({ ...it, answer: v })} />
                  </div>
                )} />
            </Section>

            <Section title="Llamado final">
              <TextField label="Titulo" max={80} value={d.cta.title} disabled={ro} onChange={(v) => set({ cta: { ...d.cta, title: v } })} />
              <TextField label="Bajada" max={200} value={d.cta.subtitle} disabled={ro} onChange={(v) => set({ cta: { ...d.cta, subtitle: v } })} />
              <TextField label="Mensaje de WhatsApp" area max={300} value={d.cta.whatsapp_message} disabled={ro} onChange={(v) => set({ cta: { ...d.cta, whatsapp_message: v } })} />
            </Section>
          </div>

          <Card className="h-fit p-5">
            <h2 className="text-lg font-bold">Historial publicado</h2>
            <p className="mt-1 text-xs text-nx-muted">Restaurar copia esa version al borrador; despues hay que publicar.</p>
            <ul className="mt-3 flex flex-col gap-2 text-sm">
              {versions.map((v) => (
                <li key={v.version} className="flex items-center justify-between gap-2 border-b border-nx-line/50 pb-2">
                  <span>v{v.version} · {when(v.published_at)}</span>
                  {!ro && v.version !== page?.published_version && (
                    <button className="text-xs font-bold text-nx-accent hover:underline" disabled={busy}
                      onClick={() => (!dirty || window.confirm("Hay cambios sin guardar que se van a perder. ¿Seguir?")) && run(() => api.restoreSiteVersion("home", v.version), `Version ${v.version} copiada al borrador.`)}>
                      Restaurar
                    </button>
                  )}
                </li>
              ))}
              <li className="flex items-center justify-between gap-2">
                <span className="text-nx-muted">v0 · contenido original</span>
                {!ro && <button className="text-xs font-bold text-nx-accent hover:underline" disabled={busy}
                  onClick={() => (!dirty || window.confirm("Hay cambios sin guardar que se van a perder. ¿Seguir?")) && run(() => api.restoreSiteVersion("home", 0), "Contenido original copiado al borrador.")}>Restaurar</button>}
              </li>
            </ul>
          </Card>
        </div>
      )}
    </AdminLayout>
  );
}
