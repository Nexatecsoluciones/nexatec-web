"use client";

import Script from "next/script";
import { useCallback, useRef, useState } from "react";

declare global {
  interface Window {
    turnstile?: {
      render: (
        container: HTMLElement,
        options: { sitekey: string; callback: (token: string) => void; "error-callback"?: () => void },
      ) => string;
      reset: (widgetId?: string) => void;
    };
  }
}

/**
 * Widget real de Cloudflare Turnstile. La verificacion SIEMPRE ocurre
 * server-side (ver app/security/turnstile.py); esto solo genera el token
 * que se envia en el body de /api/auth/login. Sin site key configurada,
 * no renderiza nada y el login fallara la verificacion server-side --
 * exactamente el comportamiento esperado (no hay bypass de cliente).
 */
export function useTurnstileToken(siteKey: string) {
  const [token, setToken] = useState<string>("");
  const widgetId = useRef<string | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const renderWidget = useCallback(() => {
    if (!siteKey || !window.turnstile || !containerRef.current) return;
    widgetId.current = window.turnstile.render(containerRef.current, {
      sitekey: siteKey,
      callback: (t: string) => setToken(t),
      "error-callback": () => setToken(""),
    });
  }, [siteKey]);

  const reset = useCallback(() => {
    if (window.turnstile && widgetId.current) window.turnstile.reset(widgetId.current);
    setToken("");
  }, []);

  const widget = siteKey ? (
    <>
      <Script
        src="https://challenges.cloudflare.com/turnstile/v0/api.js"
        strategy="afterInteractive"
        onLoad={renderWidget}
      />
      <div ref={containerRef} />
    </>
  ) : null;

  return { token, widget, reset };
}
