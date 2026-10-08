import Link from "next/link";
import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-[28px] border border-nx-line bg-nx-card shadow-[0_28px_90px_rgba(0,0,0,0.3)] ${className}`}
    >
      {children}
    </div>
  );
}

export function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex w-fit items-center rounded-full bg-nx-accent/10 px-3 py-1 text-xs font-bold uppercase tracking-wide text-nx-accent">
      {children}
    </span>
  );
}

type ButtonVariant = "primary" | "secondary" | "outline";

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-gradient-to-b from-nx-accent to-nx-accent-2 text-[#06352f] shadow-[0_14px_34px_rgba(81,234,216,0.22)]",
  secondary: "bg-white/5 border border-nx-line text-nx-text",
  outline: "bg-transparent border border-nx-accent/40 text-nx-accent",
};

export function Button({
  children,
  variant = "primary",
  type = "button",
  onClick,
  disabled,
  className = "",
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  type?: "button" | "submit";
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex min-h-[48px] items-center justify-center rounded-full px-6 font-bold transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-60 ${variantClasses[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

export function TopNav({ activePath }: { activePath: string }) {
  // Navegacion publica: nada del Control Center aca (tiene su propio menu).
  const links = [
    { href: "/", label: "Inicio" },
    { href: "/demo", label: "Probar demo" },
    { href: "/portal", label: "Mis sistemas" },
  ];

  return (
    <header className="sticky top-0 z-50 border-b border-nx-line bg-[#022b25]/85 backdrop-blur-lg">
      <div className="mx-auto flex min-h-[76px] w-full max-w-[1180px] items-center justify-between gap-6 px-5">
        <Link href="/" className="flex items-center gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-2xl bg-gradient-to-b from-[#0f5d51] to-[#0b4b42]">
            <NexatecMark />
          </span>
          <span className="text-lg font-extrabold tracking-tight">NEXATEC</span>
        </Link>
        <nav className="flex items-center gap-6 text-sm font-semibold text-[#dce8e5]">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={l.href === activePath ? "text-nx-accent" : "hover:text-nx-accent"}
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}

const ADMIN_NAV = [
  { href: "/admin", label: "Dashboard" },
  { href: "/admin/clientes", label: "Clientes" },
  { href: "/admin/usuarios", label: "Usuarios" },
  { href: "/admin/productos", label: "Productos" },
  { href: "/admin/demos", label: "Demos" },
  { href: "/admin/planes", label: "Planes" },
  { href: "/admin/sitio", label: "Sitio web" },
  { href: "/admin/pagos", label: "Pagos" },
  { href: "/admin/jobs", label: "Jobs" },
  { href: "/admin/estado", label: "Estado" },
  { href: "/admin/seguridad", label: "Seguridad" },
];

export function AdminLayout({
  activePath,
  userEmail,
  userRole,
  onLogout,
  children,
}: {
  activePath: string;
  userEmail: string;
  userRole: string;
  onLogout: () => void;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-1 flex-col lg:flex-row">
      <aside className="flex flex-col gap-1 border-b border-nx-line bg-black/20 p-4 lg:w-64 lg:border-b-0 lg:border-r lg:p-6">
        <Link href="/admin" className="mb-6 flex items-center gap-3 px-2">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-b from-[#0f5d51] to-[#0b4b42]">
            <NexatecMark />
          </span>
          <div>
            <p className="text-sm font-extrabold leading-tight">NEXATEC</p>
            <p className="text-[10px] uppercase tracking-widest text-nx-muted">Control Center</p>
          </div>
        </Link>
        <nav className="flex flex-row flex-wrap gap-1 lg:flex-col">
          {ADMIN_NAV.map((item) => {
            const isActive = item.href === "/admin" ? activePath === "/admin" : activePath.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-xl px-3 py-2 text-sm font-semibold transition ${
                  isActive ? "bg-nx-accent/15 text-nx-accent" : "text-nx-muted hover:bg-white/5 hover:text-nx-text"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex min-h-[64px] items-center justify-between gap-4 border-b border-nx-line bg-black/10 px-5">
          <p className="text-sm font-bold uppercase tracking-widest text-nx-muted">NEXATEC Control Center</p>
          <div className="flex items-center gap-3 text-sm">
            <div className="text-right">
              <p className="font-semibold">{userEmail}</p>
              <p className="text-xs text-nx-muted">{userRole}</p>
            </div>
            <Button variant="secondary" onClick={onLogout} className="min-h-[38px] px-4 text-xs">
              Salir
            </Button>
          </div>
        </header>
        <main className="flex-1 p-5 lg:p-8">{children}</main>
      </div>
    </div>
  );
}

export function NexatecMark() {
  return (
    <svg viewBox="0 0 240 240" className="h-6 w-6">
      <g fill="none" stroke="#51EAD8" strokeWidth="26" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="60,182 60,58 180,182 180,58" />
      </g>
      <circle cx="180" cy="58" r="15" fill="#B9F9EF" />
      <circle cx="60" cy="182" r="9" fill="#2CC3B2" />
    </svg>
  );
}
