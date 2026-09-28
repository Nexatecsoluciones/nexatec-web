# Cloudflare (Tunnel + Access) para staging

## Estado actual (en vivo)

- URL: **https://staging.nexatecpy.com** -- ONLINE.
- Tunnel: `nexatec-platform` (ID `dd49f962-6860-4db8-8d6a-894005746b01`) --
  HEALTHY, 1 replica activa en `servidor-aplicaciones` (este servidor).
- Ruta: `staging.nexatecpy.com` -> `http://127.0.0.1:4302` (HTTP, no HTTPS
  -- ver incidente 3 mas abajo).
- DNS: CNAME `staging.nexatecpy.com` -> `dd49f962-....cfargotunnel.com`,
  Proxied. Verificado con `dig` contra 1.1.1.1, 8.8.8.8 y 9.9.9.9.
- **Cloudflare Access: NO CONFIGURADO.** El sitio esta publico en
  Internet ahora mismo. Se intento configurar (Zero Trust -> Access ->
  Applications -> Self-hosted) pero el dashboard de Cloudflare devolvio
  un error de JavaScript propio ("Error al ejecutar 'removeChild' en
  'Node'") de forma persistente al guardar la politica, incluso despues
  de recargar -- un bug del lado de Cloudflare, no de esta configuracion.
  Decision explicita del usuario: continuar sin Access por ahora y
  retomarlo mas adelante (probar otro navegador/ventana de incognito, o
  reintentar cuando el dashboard lo permita). Ver el riesgo activo
  correspondiente en `docs/SECURITY.md`.

## Incidentes durante la configuracion (resueltos, documentados por transparencia)

1. **Token del tunnel visible en el chat de esta sesion.** Al correr
   `cloudflared service install <token>` se genero ademas, por error, un
   segundo proceso en foreground (`cloudflared tunnel run --token ...`)
   corrido manualmente. Un diagnostico con `ps aux` capturo ese argumento
   completo. El proceso duplicado se mato de inmediato; el usuario decidio
   explicitamente NO rotar el token dado el alcance limitado (un token de
   tunnel solo permite correr un conector para ESE tunnel, no da acceso a
   la cuenta de Cloudflare). Queda documentado como decision consciente,
   no como omision.
2. **Ruta creada por error sobre el apex `nexatecpy.com`** en vez de
   `staging.nexatecpy.com` (confusion en el campo de hostname del
   formulario "Add route"). Se detecto con `dig` inmediatamente, y se
   corrigio editando el registro DNS (cambiar `Name` de `nexatecpy.com` a
   `staging`) -- confirmado que el apex volvio a no resolver.
3. **502 / `tls: first record does not look like a TLS handshake`**:
   la ruta del tunnel quedo creada con Service Type `HTTPS` por defecto
   (`https://127.0.0.1:4302`), pero Next.js standalone en este servidor
   sirve HTTP plano en ese puerto (TLS lo termina Cloudflare en el borde,
   no hace falta HTTPS interno). Se corrigio cambiando el Service Type de
   la ruta a `HTTP`.

**Aprendizaje operativo importante**: la UI de "Routes" del Tunnel
(antes "Public Hostname") **no siempre crea el registro DNS
automaticamente** -- cuando no lo hace, muestra el aviso "This domain
isn't a zone on your account" incluso para un subdominio de una zona que
si existe. La via confiable es ir directo a **DNS -> Records** del
dominio y crear/editar el CNAME ahi a mano.

## Estado del dominio (verificado, no asumido)

`nexatecpy.com` esta delegado a Cloudflare (`lee.ns.cloudflare.com`,
`hadlee.ns.cloudflare.com`) pero **no tiene ningun registro DNS activo
todavia** (ni A, ni CNAME, en el apex ni en `www`) -- verificado con `dig`
contra el resolver local y contra `1.1.1.1` directamente, y con `curl`
(`Could not resolve host`). No hay GitHub Pages configurado en el repo
(`api.github.com/.../pages` devuelve 404) ni ninguna config de nginx/certbot
para ese dominio en este servidor. En otras palabras: **hoy nexatecpy.com
no esta servido en ningun lado**. Esto no cambia las reglas (no tocar el
apex/`www` sin autorizacion explicita), pero significa que agregar
`staging` no puede romper nada que ya funcione ahi.

## Por que Cloudflare Tunnel (no exposicion directa)

`cloudflared` abre una conexion **saliente** desde este servidor hacia el
borde de Cloudflare. No se abre ningun puerto entrante, no se toca
`firewalld`, no se toca ninguna Security List/NSG de OCI. El origen
publicado es siempre `http://127.0.0.1:4302` (nunca `4301`, nunca
PostgreSQL).

## Metodo elegido: tunnel remotely-managed

Se usa un tunnel **gestionado desde el dashboard** (no
`cloudflared tunnel login` + `config.yml` local), porque:

- Es el metodo que Cloudflare recomienda actualmente para tunnels de
  aplicacion (Zero Trust > Networking > Tunnels).
- La configuracion de ingress (que hostname va a que origen) se administra
  en el dashboard, no en un archivo YAML que haya que mantener a mano en
  el servidor.
- El "Public Hostname" que se configura ahi crea automaticamente el
  registro DNS correspondiente -- no hace falta tocar DNS por separado.

## cloudflared instalado

Binario oficial `linux-arm64` descargado directo desde los releases de
GitHub de `cloudflared` (no hay paquete `.rpm`, y Oracle Linux no tiene
`apt`) e instalado en `/usr/local/bin/cloudflared`. Verificado con
`cloudflared --version`. Todavia **sin autenticar y sin tunnel
configurado** -- eso requiere las acciones de dashboard de mas abajo.

## Pasos que solo se pueden hacer desde el Dashboard de Cloudflare

**No compartas el token del conector conmigo.** Corre el comando de
instalacion vos mismo en una terminal de este servidor.

### 1. Crear el Tunnel

1. Entra a **https://one.dash.cloudflare.com/** (Zero Trust dashboard) con
   la cuenta de Cloudflare donde esta `nexatecpy.com`.
2. Menu izquierdo: **Networking → Tunnels**.
3. **Create a tunnel** → tipo **Cloudflared** → Next.
4. Nombre del tunnel: `nexatec-platform`.
5. Cloudflare te muestra un comando de instalacion con un token largo,
   algo como:
   ```
   sudo cloudflared service install eyJhIjoiXXXXXXXX...
   ```
6. **Copia ese comando completo** y corrélo VOS en una terminal de este
   servidor (SSH o la terminal de VSCode que ya usas) -- no me lo pegues
   a mi. Eso instala y arranca el servicio `cloudflared` como systemd unit
   propia, autenticado contra tu cuenta.
7. Confirmá que quedo bien corriendo:
   ```
   sudo systemctl status cloudflared --no-pager
   ```
   Deberia decir `active (running)`. Si querés que yo lo verifique,
   avisame y lo reviso desde aca (sin ver el token).

### 2. Public Hostname (dentro del mismo tunnel)

1. En la pagina del tunnel `nexatec-platform`, pestaña **Public Hostname**
   → **Add a public hostname**.
2. Subdomain: `staging`
3. Domain: `nexatecpy.com`
4. Path: dejar vacio.
5. Service Type: `HTTP`
6. URL: `127.0.0.1:4302`
7. Guardar.

Esto crea automaticamente el DNS CNAME de `staging.nexatecpy.com` hacia el
tunnel (proxied, naranja) -- no hace falta tocar la pagina de DNS por
separado. **No toques ninguna otra ruta que ya exista en este tunnel** (si
el tunnel es nuevo, no debería haber ninguna).

### 3. Cloudflare Access (proteger staging mientras se desarrolla)

1. Zero Trust dashboard → **Access → Applications → Add an application**.
2. Tipo: **Self-hosted**.
3. Application name: `NEXATEC Staging`.
4. Session duration: la que prefieras (ej. 24h).
5. Application domain: `staging.nexatecpy.com` (sin path, aplica a todo).
6. Siguiente: **Policies** → **Add a policy**.
   - Policy name: `Solo NEXATEC`.
   - Action: **Allow**.
   - Include: **Emails** → ingresa ahi el/los emails autorizados (el tuyo,
     y el de quien mas necesite ver staging). **Yo no necesito saber cual
     email pusiste ahi** -- ese dato lo maneja Cloudflare, no hace falta
     decirmelo.
   - Si Cloudflare te ofrece "One-time PIN" como metodo de login para esos
     emails, es valido usarlo para este caso (staging interno).
7. Guardar. Cloudflare Access queda delante de TODO `staging.nexatecpy.com`,
   incluyendo `/api/*` -- es una capa adicional, no reemplaza el login de
   NEXATEC (`/login`, RBAC), que sigue aplicando normalmente despues de
   pasar Access.

### 4. Confirmar que no se rompio nada existente

Antes de terminar, en el dashboard: **Networking → Tunnels** -- confirma
que no hay otro tunnel viejo con rutas para `hesedpy.com` o cualquier otra
cosa que dejes de ver. Si aparece alguno, **no lo toques** y avisame para
documentarlo antes de seguir.

## Turnstile (formulario de login)

Ver `docs/SECURITY.md` seccion Turnstile. Mientras no haya claves reales,
staging usa las claves de PRUEBA publicas de Cloudflare (documentadas
oficialmente, no son un secreto):

- Site key: `1x00000000000000000000AA` (siempre aprueba)
- Secret key: `1x0000000000000000000000000000000AA` (siempre aprueba)

Verificado que la llamada real a `https://challenges.cloudflare.com/turnstile/v0/siteverify`
efectivamente distingue: con la secret "always-blocks"
(`2x0000000000000000000000000000000AA`) devuelve `success: false`; con la
de arriba, `success: true`. El mecanismo de verificacion es real, no un
bypass -- solo la respuesta de Cloudflare esta fijada por la naturaleza de
esa secret de prueba.

Para pasar a claves reales (cuando quieras dejar de depender de las de
prueba, o antes de exponer staging sin Cloudflare Access):

1. Zero Trust dashboard → **Turnstile** → **Add site**.
2. Domain: `staging.nexatecpy.com` (agregar tambien `nexatecpy.com` cuando
   exista produccion).
3. Widget mode: Managed (recomendado).
4. Copia la **Site Key** → va en `apps/web/.env.production.local` como
   `NEXT_PUBLIC_TURNSTILE_SITE_KEY` (publica, se puede versionar en un
   `.env.example`, no en el `.env.production.local` real committeado).
5. Copia la **Secret Key** → va en `/etc/nexatec/api-staging.env` como
   `NEXATEC_TURNSTILE_SECRET_KEY` (esta si es secreta, nunca en el repo).
6. Reconstruir la web (`./scripts/build-standalone.sh`) y reiniciar
   `nexatec-api-staging.service`.

## Que NO se hizo (a proposito)

- No se toco DNS de `nexatecpy.com`/`www` (no existe, no habia nada que
  tocar).
- No se publico `api.nexatecpy.com` ni ningun hostname hacia `4301`.
- No se uso Global API Key de Cloudflare en ningun momento.
- No se pidio ni se recibio ningun token de Cloudflare en esta conversacion.
