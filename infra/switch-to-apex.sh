#!/usr/bin/env bash
# Pasa la plataforma de https://staging.nexatecpy.com a https://nexatecpy.com.
# Orden seguro: 1) DNS + ruta del tunnel NEXATEC para nexatecpy.com y www
# (solo el tunnel dd49f962, nunca el de HESED ni el de kokuegreen),
# 2) espera a que el apex responda, 3) recien ahi cambia URLs, rebuild y
# reinicio (que activa la redireccion www/staging -> apex).
# Correr como opc:  bash /home/opc/nexatec-web/infra/switch-to-apex.sh
set -euo pipefail
cd /home/opc/nexatec-web/apps/api

echo "1) DNS + tunnel"
( set -a; . /etc/nexatec/api-staging.env; set +a
  .venv/bin/python -c "
from app.services.cloudflare_dns import ensure_public_hostname_route as e
for h in ('nexatecpy.com', 'www.nexatecpy.com'):
    e(h); print('   ok', h)" )

# El resolver local de OCI cachea el NXDOMAIN de antes (hasta 30 min): se
# resuelve contra 1.1.1.1 y se fuerza la IP con --resolve.
cf_curl() {
  local url=$1 host ip
  host=$(echo "$url" | sed -E 's#https://([^/]+).*#\1#')
  ip=$(dig +short "$host" @1.1.1.1 | grep -E '^[0-9.]+$' | head -1)
  curl -s -o /dev/null -w '%{http_code} %{redirect_url}' ${ip:+--resolve "$host:443:$ip"} "$url" || true
}

echo "2) esperando que https://nexatecpy.com responda"
for i in $(seq 1 30); do
  code=$(cf_curl https://nexatecpy.com/ | cut -d' ' -f1)
  [ "$code" = "200" ] && break
  sleep 5
done
[ "$code" = "200" ] || { echo "   nexatecpy.com no responde (HTTP $code); no cambio nada mas"; exit 1; }
echo "   responde 200"

echo "3) URLs publicas -> https://nexatecpy.com"
sed -i \
  -e 's#^NEXATEC_CORS_ALLOWED_ORIGINS=.*#NEXATEC_CORS_ALLOWED_ORIGINS=https://nexatecpy.com#' \
  -e 's#^NEXATEC_STORAGE_PUBLIC_BASE_URL=.*#NEXATEC_STORAGE_PUBLIC_BASE_URL=https://nexatecpy.com/storage#' \
  -e 's#^NEXATEC_PUBLIC_BASE_URL=.*#NEXATEC_PUBLIC_BASE_URL=https://nexatecpy.com#' \
  /etc/nexatec/api-staging.env
sed -i 's#^NEXATEC_PUBLIC_URL=.*#NEXATEC_PUBLIC_URL=https://nexatecpy.com#' /etc/nexatec/web-staging.env

echo "4) build + reinicio"
cd /home/opc/nexatec-web/apps/web
bash scripts/build-standalone.sh > /dev/null
sudo systemctl restart nexatec-api-staging.service nexatec-web-staging.service
sleep 5

echo "5) verificacion"
for u in https://nexatecpy.com/ https://nexatecpy.com/api/health https://www.nexatecpy.com/ https://staging.nexatecpy.com/login; do
  printf '   %-40s %s\n' "$u" "$(cf_curl "$u")"
done
systemctl is-active hesed-ot.service >/dev/null && echo "   hesed-ot: activo (intacto)"
echo "Listo: https://nexatecpy.com"
