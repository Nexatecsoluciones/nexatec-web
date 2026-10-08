import type { ReactNode } from "react";
import { PublicShell } from "@/components/site";
import { CONTACT_EMAIL, LEGAL } from "@/lib/site";

export function LegalPage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <PublicShell>
      <article className="mx-auto w-full max-w-3xl px-5 py-14 text-sm leading-relaxed text-nx-muted [&_h2]:mt-8 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:text-nx-text [&_li]:ml-5 [&_li]:list-disc [&_p]:mt-3">
        <h1 className="text-3xl font-extrabold text-nx-text">{title}</h1>
        <p className="mt-3 rounded-xl border border-amber-300/40 bg-amber-400/10 p-3 text-amber-100">
          Version {LEGAL.version}. Borrador sujeto a revision legal antes de su vigencia definitiva.
        </p>
        <p>
          Responsable: {LEGAL.legalName || "NEXATEC Soluciones Tecnologicas"}{LEGAL.ruc && `, RUC ${LEGAL.ruc}`}
          {LEGAL.address && `, ${LEGAL.address}`}. Contacto: <a className="text-nx-accent" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
        </p>
        {children}
      </article>
    </PublicShell>
  );
}
