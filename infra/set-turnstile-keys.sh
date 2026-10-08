#!/usr/bin/env bash
# Carga las claves REALES de Cloudflare Turnstile (anti-bots de login, demo y
# recuperacion de contrasena) sin que la secret key quede en pantalla, en el
# historial de bash ni en ningun chat.
#
# Antes: Cloudflare -> Turnstile -> Add widget -> dominio nexatecpy.com,
# modo "Managed". Copiar Site Key y Secret Key.
# Uso (como opc):  bash /home/opc/nexatec-web/infra/set-turnstile-keys.sh
set -euo pipefail

API_ENV=/etc/nexatec/api-staging.env
WEB_DIR=/home/opc/nexatec-web/apps/web
WEB_BUILD_ENV=$WEB_DIR/.env.production.local

read -r -p "Site Key (empieza con 0x): " SITE
read -r -s -p "Secret Key (no se muestra al escribir): " SECRET; echo
[[ $SITE =~ ^0x[A-Za-z0-9_-]{10,}$ ]] || { echo "Site Key con formato invalido"; exit 1; }
[[ $SECRET =~ ^0x[A-Za-z0-9_-]{10,}$ ]] || { echo "Secret Key con formato invalido"; exit 1; }

# Prueba la secret contra Cloudflare con un token falso: una secret valida
# responde "invalid-input-response"; una invalida, "invalid-input-secret".
check=$(curl -s https://challenges.cloudflare.com/turnstile/v0/siteverify \
  --data-urlencode "secret=$SECRET" --data-urlencode "response=prueba")
if grep -q "invalid-input-secret" <<<"$check"; then
  echo "Cloudflare dice que la Secret Key no es valida; no cambie nada."; exit 1
fi

set_var() {  # archivo clave valor (reemplaza o agrega, sin mostrar el valor)
  local file=$1 key=$2 val=$3
  if grep -q "^$key=" "$file"; then
    python3 - "$file" "$key" "$val" <<'PY'
import sys
path, key, val = sys.argv[1:]
lines = open(path).read().splitlines()
open(path, "w").write("\n".join(f"{key}={val}" if l.startswith(f"{key}=") else l for l in lines) + "\n")
PY
  else
    printf '%s=%s\n' "$key" "$val" >> "$file"
  fi
}

set_var "$API_ENV" NEXATEC_TURNSTILE_SITE_KEY "$SITE"
set_var "$API_ENV" NEXATEC_TURNSTILE_SECRET_KEY "$SECRET"
set_var "$WEB_BUILD_ENV" NEXT_PUBLIC_TURNSTILE_SITE_KEY "$SITE"
unset SECRET
echo "Claves guardadas. Recompilando la web (unos minutos)..."

cd "$WEB_DIR" && bash scripts/build-standalone.sh > /dev/null
sudo systemctl restart nexatec-api-staging.service nexatec-web-staging.service
sleep 5
systemctl is-active --quiet nexatec-api-staging.service nexatec-web-staging.service && echo "Servicios activos."
systemctl is-active --quiet hesed-ot.service && echo "hesed-ot: activo (intacto)."
echo "Listo. Proba entrar en https://nexatecpy.com/login: tiene que aparecer el recuadro de Cloudflare."
