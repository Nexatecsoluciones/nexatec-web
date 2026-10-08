"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useErp, useLoad } from "@/components/erp";
import type { HrEmployee, HrSite, Page } from "@/lib/erp";

const TABS = [
  { href: "", label: "Personal", perm: "hr:read" },
  { href: "/asistencia", label: "Asistencia", perm: "hr:read" },
  { href: "/adelantos", label: "Adelantos y ausencias", perm: "hr:read" },
  { href: "/planillas", label: "Planillas", perm: "hr:amounts" },
  { href: "/obras", label: "Obras", perm: "hr:read" },
  { href: "/config", label: "Configuracion", perm: "hr:payroll" },
];

export function HrTabs() {
  const { base, can } = useErp();
  const pathname = usePathname();
  const root = `${base}/rrhh`;
  return (
    <nav className="mb-5 flex flex-wrap gap-2 border-b border-nx-line pb-3">
      {TABS.filter((t) => can(t.perm)).map((t) => {
        const href = `${root}${t.href}`;
        const active = t.href === "" ? pathname === root : pathname.startsWith(href);
        return (
          <Link key={t.href} href={href}
            className={`rounded-full px-4 py-1.5 text-sm font-semibold ${active ? "bg-nx-accent text-[#06352f]" : "border border-nx-line text-nx-muted hover:text-nx-text"}`}>
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}

/** Obras y personal activo: los usan casi todas las pestanas. */
export function useHrRefs() {
  const { client } = useErp();
  return useLoad(async () => ({
    sites: await client.get<HrSite[]>("/hr/sites"),
    employees: (await client.get<Page<HrEmployee>>("/hr/employees", { limit: 500 })).items,
  }), []);
}

export function todayISO(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Asuncion" });
}

export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}
