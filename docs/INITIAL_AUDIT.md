# Auditoría inicial — nexatec-web

Fecha: 2026-09-28
Autor: sesión de Claude Code (agente), a pedido de Javier Ferreira / NEXATEC

## 1. Qué se encontró en el repositorio

- Repo: `github.com/Nexatecsoluciones/nexatec-web`, rama `main`, historial de 12 commits, working tree limpio.
- Contenido completo (antes de esta intervención): **3 archivos**:
  - `index.html` (1100 líneas) — landing page completa de una sola página. Todo el CSS está **inline** en un `<style>` dentro del `<head>`, y hay un `<script>` inline con la función `openWhatsApp()`.
  - `styles.css` (825 líneas) — **hoja de estilos huérfana**: `index.html` no la enlaza en ningún momento (`grep` no encuentra `stylesheet.*styles.css`). Define un diseño distinto (paleta azul/cian, componentes tipo "hero-panel" con mockup de dashboard, `pricing-grid`, `product-section") que no corresponde a ninguna sección del HTML actual. Es artefacto de una iteración de diseño anterior que quedó en el repo sin usarse.
  - `README.md` — una línea, sin instrucciones de build/deploy.
- No hay `package.json`, build tooling, tests, CI, `.gitignore`, ni backend de ningún tipo. Es un sitio 100% estático.
- El sitio real (`index.html`) usa: tipografía Poppins (Google Fonts), paleta verde esmeralda/teal (`--accent: #51EAD8`), secciones: Header/Nav, Hero, Servicios (ERP/CRM/BI/Automatización), Planes (3 planes con precios en guaraníes), Nosotros, Contacto, Footer. El único canal de conversión es WhatsApp (`wa.me` / `web.whatsapp.com`, número `595981813971`) y un `mailto:`.
- No se encontró dónde está desplegado actualmente `nexatec-web` (no hay `CNAME`, GitHub Pages workflow, ni config de Cloudflare Pages/Workers en el repo). No se tocó ni se asumió nada sobre su hosting actual.

## 2. Entorno de ejecución de esta sesión (servidor OCI, usuario `opc`)

Se inspeccionó **antes de instalar o modificar nada**, según lo pedido:

- SO: Oracle Linux Server 10.2 (aarch64), 4 vCPU, 22 GiB RAM (12 GiB libres), disco `/`: 25 GB total, **solo 8.8 GB libres (65% usado)**.
- `sudo` sin contraseña disponible para el usuario `opc` (`sudo -n true` → OK).
- **Este servidor YA está dedicado en producción a otro sistema, no relacionado con NEXATEC**: `hesed-ot-sistema` (Node, proceso `dist/server.cjs`) escuchando en `127.0.0.1:3000`, con PostgreSQL local (`127.0.0.1:5432`) y Nginx (80/443) hace de reverse proxy con TLS de **Let's Encrypt/Certbot** para el dominio `hesedpy.com` / `www.hesedpy.com`. Esto coincide con el proyecto HESED OT documentado en memoria de sesiones anteriores.
- **No hay Cloudflare Tunnel instalado ni configurado** en este servidor (`cloudflared` no existe, no hay servicio systemd, no se encontró ningún `config.yml`). El único mecanismo TLS/DNS activo es Certbot apuntando a `hesedpy.com`.
- **Docker no está instalado** (`docker: command not found`). Ni `docker`, ni `docker compose`, ni contenedores de ningún tipo corriendo.
- `firewalld` activo, solo permite `ssh`, `http`, `https` — ningún puerto adicional abierto a Internet.
- Puertos ocupados relevantes detectados (`ss -lntp` con sudo): `22` (sshd), `80`/`443` (nginx), `3000` (hesed-ot-sistema, solo loopback), `5432` (postgres, solo loopback), `111` (rpcbind), `4330`/`44321` (agente de monitoreo Oracle Cloud/pcp), más puertos efímeros de VS Code Server. Ninguno de estos se tocó.
- No se encontró clave SSH privada configurada para GitHub (`~/.ssh` solo tiene `authorized_keys`, sin `id_ed25519`/`id_rsa`); `ssh -T git@github.com` → `Permission denied (publickey)`. El clonado se hizo por **HTTPS de solo lectura** (el repo es público), sin necesidad de credenciales. **No hay forma de hacer `git push` desde aquí todavía** — se necesitará un token o una clave de despliegue cuando el usuario decida.

### Conclusión operativa clave

Este servidor Oracle Cloud (`opc@...`) es la **máquina de producción de HESED OT**, no un servidor genérico ni preparado para alojar la nueva plataforma de NEXATEC. El brief original asume "mi propio servidor" con Cloudflare Tunnel ya en uso y espacio para varios PostgreSQL + Docker Compose con 6-8 servicios nuevos. Antes de instalar Docker, levantar 3 instancias de PostgreSQL, MinIO, Valkey, etc. **en esta misma máquina que ya sirve tráfico real de otro cliente**, hace falta una decisión explícita del usuario (ver sección de próximos pasos al final de la respuesta). Se decidió avanzar con lo seguro y reversible (FASE 0 y FASE 1, contenidas al repo Git) y pausar antes de tocar infraestructura del servidor compartido.

## 3. FASE 0 — completada

- [x] Clonado el repo (HTTPS, solo lectura) en `/home/opc/nexatec-web`.
- [x] Analizado todo el código existente (`index.html`, `styles.css`, `README.md`).
- [x] Ejecutado el sitio localmente (`python3 -m http.server` en `127.0.0.1:8181`, sin exponerlo) → `HTTP 200`.
- [x] Tag de respaldo del estado funcional: `backup/pre-platform-2026-09-28` sobre `main` (commit `e59033e`).
- [x] Rama de trabajo creada: `feature/nexatec-platform`.
- [x] Inventario de puertos, procesos, Docker y Cloudflared en el servidor (arriba).

## 4. FASE 1 — en progreso en esta misma rama

Objetivo: conservar visualmente el sitio actual pero mejorar su estructura, sin migrar todavía a Next.js/TypeScript (eso corresponde a FASE 2, que depende de la decisión de infraestructura). Cambios aplicados — detallados en el commit correspondiente:

- Separación de CSS inline → `styles.css` real y enlazado (reemplaza el archivo huérfano, que quedó preservado en el historial de Git bajo el tag de backup).
- Separación del JS inline → `script.js`.
- Metadatos: Open Graph, canonical, `theme-color`, `lang` correcto, favicon SVG inline basado en el logo existente.
- `robots.txt` y `sitemap.xml`.
- Sin cambios visuales de fondo: misma paleta, misma tipografía, mismo contenido, mismos textos y precios.
