# Prueba E2E de la interfaz del ERP (navegador real)

`apps/web/e2e/erp.e2e.mjs` maneja un Chromium headless contra **staging**:
login → portal → "Probar demo" → recorre las 8 pantallas del ERP → hace una
venta completa **por la interfaz** (pedido, confirmar, entregar, facturar) →
registra el cobro aplicado a esa factura → emite una nota de crédito (devolución) → abre la vista de impresión, verifica la marca de agua y genera el PDF → verifica que el balance general
diga "Activo = Pasivo + Patrimonio" → revisa que en un celular (390 px) la
página no desborde. Falla si hay errores de JavaScript en consola. Guarda
capturas en `E2E_SHOTS` (por defecto `apps/web/e2e/shots/`, ignorado por git).

Última corrida (2026-10-08): **19/19 OK (incluye CRM y RR.HH.: planilla cerrada con recibos y marcación desde un celular), sin errores de consola**. Además `admin-first-login.e2e.mjs` (primer ingreso de superadmin con cambio de contraseña, MFA con QR y editor del sitio sin publicar): 6/6.

## Cómo correrla

El navegador NO está instalado en el servidor (es compartido con
hesed-ot-sistema); se instala a nivel usuario en un directorio descartable,
y las 9 librerías del sistema que le faltan a Chromium se extraen de los RPM
**sin instalarlas** (no se toca el sistema):

```bash
W=/tmp/nexatec-e2e && mkdir -p $W/rpms $W/libs && cd $W
npm init -y >/dev/null && npm i playwright@1
PLAYWRIGHT_BROWSERS_PATH=$W/browsers npx playwright install chromium
cd $W/rpms && dnf download atk at-spi2-atk at-spi2-core alsa-lib mesa-libgbm \
  libXcomposite libXdamage libXfixes libXrandr libXi
cd $W/libs && for r in $W/rpms/*.rpm; do rpm2cpio "$r" | cpio -idm --quiet; done

# Tenant descartable (demo con empresa ficticia + usuario CLIENT_ADMIN).
# No pasa por el endpoint de demos: no crea subdominios ni toca Cloudflare.
cd /home/opc/nexatec-web/apps/api && source .venv/bin/activate
set -a && source /etc/nexatec/api-staging.env && set +a
python scripts/e2e_tenant.py create $W/cred.json

cp /home/opc/nexatec-web/apps/web/e2e/erp.e2e.mjs $W/ && cd $W
LD_LIBRARY_PATH=$W/libs/usr/lib64 PLAYWRIGHT_BROWSERS_PATH=$W/browsers \
  E2E_SHOTS=$W/shots/ node erp.e2e.mjs cred.json

# Si el DNS local del servidor todavia no resuelve nexatecpy.com (cache de
# NXDOMAIN), agregar: E2E_HOST_RULES="MAP nexatecpy.com 104.21.50.187"

# Siempre al final: borra base física, filas y el archivo de credenciales.
cd /home/opc/nexatec-web/apps/api && python scripts/e2e_tenant.py destroy $W/cred.json
```

`destroy` se niega a borrar un tenant cuyo slug no empiece con `e2e-`.
Requiere que staging use las claves de **prueba** de Turnstile (como hoy);
con claves reales, el login automatizado no pasa el captcha.

## Sitio público (`apps/web/e2e/public-site.e2e.mjs`)

Home con todas las secciones y WhatsApp correcto, menú público sin enlaces
del Control Center, aviso de cookies, **pedido de demo real** (exige aceptar
privacidad y queda registrado con la versión aceptada), páginas legales y
móvil sin desborde. Uso: `node public-site.e2e.mjs e2e-public-<algo>@example.com`
y después borrar el pedido:
`sudo -u postgres psql -d nexatec_control -c "delete from demo_requests where contact_email like 'e2e-public-%@example.com'"`.
Última corrida: 5/5. Detectó un error 500 real (comparación INET vs texto en
el límite por IP) que los tests unitarios no cubrían; corregido y con test.

## Producción con Turnstile real

Desde que `nexatecpy.com` usa claves reales de Turnstile, el navegador
automatizado no puede pasar el desafío del login (es lo esperado). Por eso
`scripts/e2e_tenant.py create` emite además una sesión en el servidor
(`session_cookie` en el JSON, vence en 2 h) y `erp.e2e.mjs` la usa si está.
Requiere acceso al servidor, no abre nada nuevo. El login con Turnstile se
prueba a mano. `admin-first-login.e2e.mjs` y el envío del formulario de demo
de `public-site.e2e.mjs` necesitan un entorno con claves de prueba.
