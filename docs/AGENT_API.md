# API para agentes (ChatGPT) — pruebas de punta a punta en un sandbox

Un GPT de ChatGPT se conecta por API a una **demo propia (sandbox)** con datos
ficticios, recorre los flujos del ERP y propone mejoras. Nunca ve la demo de
un cliente ni datos de producción.

## Seguridad

- Llave `nxk_…` (Authorization: Bearer). En la base solo está su hash; se
  muestra una vez. Vence (90 días por defecto), se revoca, 120 pedidos/min.
- La llave pertenece a un usuario de servicio (Administrador del sandbox,
  sin contraseña usable) y está **atada a un acceso DEMO**: solo sirve en
  `/api/erp/{ese acceso}/…`. Otro acceso → 404; un acceso de producción → 403;
  Control Center, portal y cuenta → 401 (ahí la llave no autentica).
- Mismos permisos, validaciones y auditoría que la interfaz.
- **No hay endpoint para borrar ni reiniciar.** Reiniciar el sandbox lo hace
  una persona por CLI y solo funciona sobre empresas `sandbox-agente-*`.

## Endpoints

| | |
|---|---|
| `GET /api/agent/openapi.json` | Especificación para "Acciones" del GPT (pública, sin secretos). Universal: 4 operaciones. `?module=base\|ventas\|compras\|contabilidad\|crm\|rrhh` agrega las rutas de ese módulo (≤30). |
| `GET /api/agent/whoami` | Demo, rol y permisos de la llave. |
| `GET /api/agent/guide` | Reglas del sistema, flujos para probar, qué evaluar y cómo reportar. |
| `GET /api/agent/catalog?module=` | Todas las rutas del ERP con parámetros y cuerpos resumidos. |
| `POST /api/agent/call` | `{method, path, query?, body?}` → ejecuta esa ruta del ERP en el sandbox. Devuelve `{status, ok, data}`. |

## Crear el sandbox y la llave (en el servidor)

```bash
cd /home/opc/nexatec-web/apps/api && set -a && . /etc/nexatec/api-staging.env && set +a
.venv/bin/python -m app.cli create-agent-sandbox --name "Sandbox ChatGPT"
```

Imprime el `system_access_id` y la llave (una sola vez). Otros comandos:

```bash
.venv/bin/python -m app.cli list-agent-keys
.venv/bin/python -m app.cli agent-key <system_access_id> --name "Otra llave" --days 30
.venv/bin/python -m app.cli revoke-agent-key nxk_XXXXXXXX
.venv/bin/python -m app.cli reset-agent-sandbox <system_access_id> --yes   # vuelve a los datos de demo
```

## Configurar el GPT

ChatGPT → Explorar GPTs → Crear → Configurar:

1. **Acciones → Crear nueva acción → Importar desde URL:**
   `https://nexatecpy.com/api/agent/openapi.json`
2. **Autenticación:** Clave de API → Tipo de autenticación **Bearer** → pegar la llave.
3. **Política de privacidad:** `https://nexatecpy.com/privacidad`
4. **Instrucciones** (pegar):

```
Sos un consultor de producto y QA del ERP NEXATEC para PyMEs de Paraguay.
Trabajás SOLO por la acción de la API, en una demo con datos ficticios.

Siempre:
1. Empezá llamando whoami y guide. Usá catalog (por módulo) para saber qué
   rutas existen y qué campos piden antes de llamar.
2. Ejecutá los flujos completos con call (como lo haría un usuario real),
   anotando cada pedido, la respuesta y cuántos pasos llevó.
3. Probá también errores: datos inválidos, estados no permitidos, montos
   límite, fechas, permisos.
4. No inventes resultados: si algo no se pudo probar, decilo.

Evaluá: pasos de más, datos repetidos, valores por defecto que faltan,
nombres inconsistentes entre módulos, mensajes de error poco claros,
reportes y filtros que una PyME necesitaría, reglas paraguayas (IVA, RUC,
IPS, timbrado), rendimiento y permisos.

Entregá:
A) Hallazgos en tabla: módulo, flujo, pasos para reproducir, actual,
   esperado, impacto (alto/medio/bajo), propuesta.
B) Un PROMPT para el desarrollador (Claude Code), en español, con los
   cambios priorizados, criterios de aceptación verificables y tests a
   agregar. Debe respetar: no tocar datos reales ni producción sin
   aprobación, no exponer secretos, documentos fiscales siguen siendo
   internos hasta SIFEN, todo cambio con tests.
```

Si ChatGPT pide más operaciones directas en lugar de `call`, se puede crear
otro GPT con `?module=ventas` (u otro módulo) en la URL de importación.
