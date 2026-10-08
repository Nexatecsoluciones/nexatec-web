# Editor del sitio público (CMS sin código)

Control Center → **Sitio web** (`/admin/sitio`). Edita los textos de la
página de inicio: aviso superior, portada, soluciones, módulos, planes,
rubros, quiénes somos, preguntas frecuentes y llamado final.

## Flujo

1. Se edita y se **guarda el borrador** (`PUT /api/admin/site/home/draft`).
   El sitio no cambia.
2. **Vista previa** muestra el borrador con el mismo componente que la home
   (`src/components/home.tsx`).
3. **Publicar** (`POST .../publish`) copia el borrador a `published`, sube
   `published_version` y deja una fila en `site_page_versions`. Los
   visitantes lo ven en menos de un minuto (`Cache-Control: max-age=30`).
4. **Descartar** vuelve el borrador a lo publicado. **Restaurar** una
   versión (o la v0 = contenido original) la copia al borrador; después hay
   que publicar.

## Reglas

- Editan y publican **SUPER_ADMIN y ADMIN**; Soporte y Facturación solo ven.
  Todo cambio queda en `audit_logs` (`SITE_DRAFT_SAVED`, `SITE_PUBLISHED`,
  `SITE_DRAFT_DISCARDED`, `SITE_VERSION_RESTORED`).
- **Solo texto plano** con largo máximo por campo, sin campos extra
  (`extra="forbid"`), sin caracteres de control. **No hay URLs editables**:
  los botones van siempre a `/demo` o a WhatsApp (solo el mensaje es
  editable). Una cuenta de admin comprometida no puede inyectar scripts ni
  links de phishing.
- `site_page_versions` es **inmutable** (trigger): es la evidencia de qué se
  mostró al público y cuándo. No tiene FK a `users` para no impedir borrar
  usuarios.
- Si la API no responde, la home muestra una portada mínima (demo +
  WhatsApp) en vez de romperse.
- Lo que no es editable a propósito: el tablero ilustrativo de la portada
  y los 3 pasos de la demo (describen el producto real) y los textos
  legales (`/privacidad`, `/terminos`, `/cookies`), que requieren datos
  legales verificados.

Código: `apps/api/app/services/site_content.py` (esquema y contenido por
defecto), `app/routers/site_content.py`, migración `b5d2e8f1a7c3`.
