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
  const links = [
    { href: "/", label: "Soluciones" },
    { href: "/portal", label: "Portal" },
    { href: "/admin", label: "Admin" },
    { href: "/admin/clientes", label: "Clientes" },
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
