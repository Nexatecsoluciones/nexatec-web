import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Build autocontenido para systemd/staging: node .next/standalone/server.js,
  // sin depender de `next start` ni de node_modules completo en el server.
  output: "standalone",
  // Un solo origen publico: nexatecpy.com. www redirige (308 conserva el
  // metodo, asi los POST a /api no se rompen). staging.* ya no existe en DNS.
  async redirects() {
    return ["www.nexatecpy.com"].map((host) => ({
      source: "/:path*",
      has: [{ type: "host" as const, value: host }],
      destination: "https://nexatecpy.com/:path*",
      permanent: true,
    }));
  },
};

export default nextConfig;
