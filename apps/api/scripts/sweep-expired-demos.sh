#!/usr/bin/env bash
# Marca EXPIRED los entitlements de demo vencidos. No interactivo, pensado
# para correr via systemd timer (ver docs/RUNBOOK.md). No hace DROP de
# nada -- solo bloquea acceso (status=EXPIRED), igual que el endpoint
# admin POST /api/admin/demos/sweep-expired.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

if [ -f /etc/nexatec/api-staging.env ] && [ "${1:-}" = "--staging" ]; then
  set -a; source /etc/nexatec/api-staging.env; set +a
else
  set -a; source .env; set +a
fi

python -m app.cli sweep-expired-demos
