#!/usr/bin/env bash
# Build de produccion (standalone) para staging/production. NO es lo mismo
# que reiniciar el servicio systemd: esto se corre cada vez que cambia el
# codigo o una variable NEXT_PUBLIC_* (esas quedan horneadas en el bundle
# del navegador en tiempo de build, no se leen en runtime).
#
# Uso: apps/web/scripts/build-standalone.sh
# Requiere apps/web/.env.local con los valores NEXT_PUBLIC_* deseados para
# este build (ver .env.example).
set -euo pipefail
cd "$(dirname "$0")/.."

npm run build

rm -rf .next/standalone/public .next/standalone/.next/static
cp -r public .next/standalone/
mkdir -p .next/standalone/.next
cp -r .next/static .next/standalone/.next/

echo "Build standalone listo en .next/standalone/server.js"
