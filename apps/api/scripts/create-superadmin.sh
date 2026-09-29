#!/usr/bin/env bash
# Crea el primer SUPER_ADMIN de NEXATEC. Interactivo -- la contrasena
# nunca se pasa como argumento (no queda en el historial de shell ni en
# `ps`). Requiere el EnvironmentFile real (staging) o .env (development).
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

if [ -f /etc/nexatec/api-staging.env ] && [ "${1:-}" = "--staging" ]; then
  set -a; source /etc/nexatec/api-staging.env; set +a
else
  set -a; source .env; set +a
fi

python -m app.cli create-superadmin
