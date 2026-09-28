import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Build autocontenido para systemd/staging: node .next/standalone/server.js,
  // sin depender de `next start` ni de node_modules completo en el server.
  output: "standalone",
};

export default nextConfig;
