"""API para agentes externos (ej. un GPT de ChatGPT) que prueban el ERP de
punta a punta en un SANDBOX de demo y proponen mejoras.

- GET  /api/agent/openapi.json            especificacion para "Acciones" de un GPT
                                          (universal de 4 operaciones, o ?module=)
- GET  /api/agent/whoami                  a que demo esta conectada la llave
- GET  /api/agent/guide                   flujos, reglas y que evaluar
- GET  /api/agent/catalog?module=         todas las rutas del ERP con sus parametros
- POST /api/agent/call                    ejecuta UNA ruta del ERP dentro de su sandbox

Seguridad: todo con la llave (Bearer nxk_...), que solo opera en SU acceso
DEMO; `call` solo reenvia a /api/erp/{ese acceso}/..., con la misma llave,
pasando por los mismos permisos que la interfaz."""

import json
import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.db import get_db
from app.models.control_plane import Tenant
from app.models.tenancy import SystemAccess
from app.security.api_keys import resolve
from app.security.erp_permissions import ROLE_LABELS, permissions_for
from app.security.session_auth import CurrentUser

router = APIRouter(prefix="/api/agent", tags=["agent"])

MODULES: dict[str, tuple[str, list[str]]] = {
    "base": ("Empresa, maestros, stock y tablero", ["context", "company", "branches", "warehouses", "units", "taxes",
                                                     "currencies", "product-categories", "products", "parties", "stock",
                                                     "dashboard"]),
    "ventas": ("Pedidos, facturas internas, cobros, notas de credito y cuentas por cobrar",
               ["sales", "invoices", "receipts", "credit-notes", "receivables"]),
    "compras": ("Ordenes de compra, facturas de proveedor, pagos y cuentas por pagar",
                ["purchases", "supplier-invoices", "supplier-payments", "payables"]),
    "contabilidad": ("Plan de cuentas, asientos, periodos y reportes", ["accounting"]),
    "crm": ("Prospectos, oportunidades, actividades y embudo", ["crm"]),
    "rrhh": ("Personal, obras, asistencia, adelantos y planillas", ["hr"]),
}


# Esquema de seguridad declarado en OpenAPI (no un header opcional suelto).
bearer = HTTPBearer(auto_error=False, scheme_name="apiKey", description="Llave de API del sandbox (nxk_...)")


def agent_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)) -> CurrentUser:
    if creds is None:
        raise HTTPException(status_code=401, detail="Falta Authorization: Bearer nxk_...",
                            headers={"WWW-Authenticate": "Bearer"})
    return resolve(db, f"Bearer {creds.credentials}")


def _segment(path: str) -> str | None:
    if not path.startswith("/api/erp/{system_access_id}/"):
        return None
    return path.split("{system_access_id}/", 1)[1].split("/")[0]


def _module_of(path: str) -> str | None:
    seg = _segment(path)
    return next((m for m, (_, segs) in MODULES.items() if seg in segs), None)


# --- Identidad y guia ---------------------------------------------------------------


@router.get("/whoami", operation_id="whoami", summary="A que demo esta conectada esta llave")
def whoami(user: CurrentUser = Depends(agent_user), db=Depends(get_db)):
    access = db.get(SystemAccess, user.api_key_access_id)
    tenant = db.get(Tenant, access.tenant_id) if access else None
    from app.models.tenancy import TenantUser
    from sqlalchemy import select
    role = db.execute(select(TenantUser.role).where(TenantUser.tenant_id == access.tenant_id,
                                                    TenantUser.user_id == user.id)).scalar_one()
    return {
        "system_access_id": str(access.id), "environment": access.environment.value,
        "company": tenant.display_name if tenant else None, "role": ROLE_LABELS.get(role, role.value),
        "permissions": sorted(permissions_for(role)), "modules": {k: v[0] for k, v in MODULES.items()},
        "how_to_call": "POST /api/agent/call con {method, path, query?, body?}; path relativo al ERP, ej. '/sales/orders'.",
    }


GUIDE = {
    "producto": ("NEXATEC ERP Cloud: ERP para PyMEs de Paraguay (multiempresa, una base por empresa). Moneda base PYG "
                 "(sin decimales), precios IVA incluido (10 %, 5 %, exenta). Estas en una DEMO con empresa y datos "
                 "FICTICIOS: podes crear, modificar y anular lo que quieras."),
    "reglas_del_sistema": [
        "Facturas y notas de credito son comprobantes INTERNOS (sin validez tributaria) hasta que SIFEN se habilite.",
        "Todo documento confirmado es inmutable: se corrige anulando o con nota de credito, nunca editando.",
        "El stock nunca queda negativo; el costo es promedio ponderado; el kardex no se edita.",
        "Cada operacion genera su asiento contable automatico (partida doble).",
        "Los periodos contables cerrados no aceptan movimientos.",
        "Montos en guaranies como texto decimal ('15000.00'); fechas 'AAAA-MM-DD'; horas 'HH:MM'.",
        "Errores: 422 (dato invalido, con explicacion), 409 (regla de negocio / estado), 403 (permiso), 404.",
    ],
    "flujos_para_probar": {
        "venta_contado": ["GET /parties?role=customer", "GET /products", "GET /warehouses",
                          "POST /sales/orders (CASH)", "POST /sales/orders/{id}/confirm", "POST /sales/orders/{id}/deliver",
                          "POST /invoices (desde el pedido)", "POST /receipts (cobro aplicado)", "GET /accounting/reports/trial-balance"],
        "venta_credito_y_cobranza": ["pedido CREDIT a cliente con limite", "facturar", "GET /receivables/aging",
                                     "cobro parcial", "GET /receivables/statement/{cliente}"],
        "devolucion": ["factura entregada", "POST /credit-notes (RETURN)", "verificar stock y saldo del cliente"],
        "compra": ["POST /purchases/orders", "confirm", "receive (parcial)", "POST /supplier-invoices",
                   "POST /supplier-payments", "GET /payables/aging"],
        "inventario": ["GET /stock/balances", "ajustes y transferencias", "kardex de un producto"],
        "contabilidad": ["GET /accounting/reports/trial-balance", "income-statement", "balance-sheet",
                         "asiento manual", "cierre de periodo"],
        "crm": ["POST /crm/leads", "POST /crm/opportunities (de prospecto)", "mover etapas",
                "POST /crm/leads/{id}/convert", "ganar/perder", "actividades y GET /crm/pipeline"],
        "rrhh": ["GET /hr/settings", "obras y personal", "POST /hr/attendance (entrada/salida de dias pasados)",
                 "aprobar/rechazar extras", "adelantos y ausencias", "GET /hr/payroll-preview",
                 "POST /hr/payrolls, close", "verificar inmutabilidad del periodo cerrado"],
    },
    "que_evaluar": [
        "Cantidad de pasos y pedidos para completar cada flujo: que se podria unir, automatizar o dar por defecto.",
        "Nombres, estados y campos: consistencia entre modulos (mismas palabras para lo mismo).",
        "Mensajes de error: claros, en español, que digan como resolver.",
        "Datos que el usuario tiene que repetir o buscar (ids, deposito, condicion de pago, fechas).",
        "Validaciones que faltan o sobran; reglas paraguayas (IVA, RUC, IPS, timbrado) mal resueltas.",
        "Reportes que una PyME esperaria y no estan; filtros, busquedas y paginacion.",
        "Rendimiento: respuestas lentas o listados demasiado grandes.",
        "Seguridad y permisos: algo que un rol no deberia poder hacer (o al reves).",
    ],
    "como_reportar": ("Por cada hallazgo: modulo, flujo, pasos para reproducir (pedidos exactos), resultado actual, "
                      "resultado esperado, impacto para el usuario (alto/medio/bajo) y propuesta concreta. Al final, "
                      "un prompt para el desarrollador (Claude Code) con los cambios priorizados, criterios de "
                      "aceptacion verificables y sin pedir que se toquen datos reales ni produccion."),
    "limites": ["Solo operas en esta demo. No existe endpoint para borrar la base ni reiniciarla: lo hace una "
                "persona en el servidor.", "120 pedidos por minuto como maximo."],
}


@router.get("/guide", operation_id="guide", summary="Guia: reglas, flujos para probar y que evaluar")
def guide(user: CurrentUser = Depends(agent_user)):
    return GUIDE


# --- Catalogo ------------------------------------------------------------------------


def _compact_schema(schema: dict, defs: dict, depth: int = 0) -> Any:
    """Esquema resumido (campo: tipo/restricciones) para que el agente arme
    pedidos validos sin descargar todo el OpenAPI."""
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return _compact_schema(defs.get(name, {}), defs, depth) if depth < 3 else name
    if "anyOf" in schema:
        parts = [_compact_schema(s, defs, depth) for s in schema["anyOf"] if s.get("type") != "null"]
        return parts[0] if len(parts) == 1 else parts
    t = schema.get("type")
    if t == "object" and "properties" in schema:
        req = set(schema.get("required", []))
        return {k + ("" if k in req else "?"): _compact_schema(v, defs, depth + 1) for k, v in schema["properties"].items()}
    if t == "array":
        return [_compact_schema(schema.get("items", {}), defs, depth + 1)]
    if "enum" in schema:
        return "|".join(map(str, schema["enum"]))
    desc = t or "any"
    for k in ("format", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "pattern"):
        if k in schema:
            desc += f" {k}={schema[k]}"
    return desc


def _catalog(app_openapi: dict, module: str | None) -> list[dict]:
    defs = app_openapi.get("components", {}).get("schemas", {})
    out = []
    for path, ops in app_openapi["paths"].items():
        mod = _module_of(path)
        if mod is None or (module and mod != module):
            continue
        rel = path.split("{system_access_id}", 1)[1]
        for method, op in ops.items():
            params = {p["name"] + ("" if p.get("required") else "?"): _compact_schema(p.get("schema", {}), defs)
                      for p in op.get("parameters", []) if p["name"] != "system_access_id"}
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            out.append({"module": mod, "method": method.upper(), "path": rel, "summary": op.get("summary"),
                        "params": params or None, "body": _compact_schema(body, defs) if body else None})
    return out


@router.get("/catalog", operation_id="catalog", summary="Rutas del ERP con parametros y cuerpos (resumidos)")
def catalog(request: Request, module: Literal["base", "ventas", "compras", "contabilidad", "crm", "rrhh"] | None = None,
            user: CurrentUser = Depends(agent_user)):
    return {"modules": {k: v[0] for k, v in MODULES.items()}, "routes": _catalog(request.app.openapi(), module)}


# --- Llamada -------------------------------------------------------------------------


class CallIn(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=300, description="Ruta del ERP relativa, ej. '/sales/orders' o '/hr/payrolls/{id}/close'")
    query: dict[str, Any] | None = None
    body: Any = None


@router.post("/call", operation_id="call", summary="Ejecuta una ruta del ERP en la demo de esta llave")
async def call(payload: CallIn, request: Request, user: CurrentUser = Depends(agent_user),
               creds: HTTPAuthorizationCredentials | None = Depends(bearer)):
    path = payload.path if payload.path.startswith("/") else "/" + payload.path
    if ".." in path or "?" in path or "#" in path or "//" in path:
        raise HTTPException(status_code=422, detail="path invalido (sin '..', '?', '#'; la query va en 'query').")
    target = f"/api/erp/{user.api_key_access_id}{path}"
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent.internal") as client:
        resp = await client.request(payload.method, target, params=payload.query,
                                    json=payload.body if payload.method != "GET" else None,
                                    headers={"Authorization": f"Bearer {creds.credentials}",
                                             "Idempotency-Key": str(uuid.uuid4())})
    try:
        data = resp.json()
    except ValueError:
        data = resp.text or None
    return {"status": resp.status_code, "ok": resp.is_success, "data": data}


# --- Especificacion para ChatGPT -----------------------------------------------------------


def _referenced_schemas(node: Any, all_schemas: dict) -> dict:
    """Solo los esquemas alcanzables desde las rutas publicadas (antes se
    publicaban los ~230 del sistema entero)."""
    found: dict = {}

    def walk(n):
        if isinstance(n, dict):
            ref = n.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in found and name in all_schemas:
                    found[name] = all_schemas[name]
                    walk(all_schemas[name])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return found


@router.get("/openapi.json", include_in_schema=False)
def agent_openapi(request: Request, module: Literal["base", "ventas", "compras", "contabilidad", "crm", "rrhh"] | None = None):
    """Publica (no contiene secretos). Sin `module`: las 4 operaciones del
    agente (recomendado: alcanza para todo el ERP). Con `module`: ademas las
    rutas de ese modulo como operaciones propias."""
    full = request.app.openapi()
    server = get_settings().public_base_url.rstrip("/")
    keep = {p: v for p, v in full["paths"].items() if p in ("/api/agent/whoami", "/api/agent/guide", "/api/agent/catalog", "/api/agent/call")}
    if module:
        for p, ops in full["paths"].items():
            if _module_of(p) == module:
                keep[p] = ops
    paths = json.loads(json.dumps(keep))
    spec = {
        "openapi": "3.1.0",
        "info": {"title": f"NEXATEC ERP - agente de pruebas{' - ' + module if module else ''}", "version": "1.0",
                 "description": "Pruebas de punta a punta en una DEMO con datos ficticios. Empezar por whoami y guide."},
        "servers": [{"url": server}],
        "paths": paths,
        "components": {"schemas": _referenced_schemas(paths, full.get("components", {}).get("schemas", {})),
                       "securitySchemes": {"apiKey": {"type": "http", "scheme": "bearer",
                                                      "description": "Llave de API del sandbox (nxk_...)"}}},
        "security": [{"apiKey": []}],
    }
    # La autenticacion la declara `security`: fuera el header/cookie sueltos.
    for ops in spec["paths"].values():
        for op in ops.values():
            if "parameters" in op:
                op["parameters"] = [x for x in op["parameters"] if x.get("name") not in ("authorization", "nexatec_session")]
    # operationId cortos y unicos (los de FastAPI son larguisimos).
    seen = set()
    for p, ops in spec["paths"].items():
        for m, op in ops.items():
            base = op.get("operationId", f"{m}_{p}")
            short = base.split("_api_")[0][:60]
            oid, n = short, 2
            while oid in seen:
                oid, n = f"{short}_{n}", n + 1
            seen.add(oid)
            op["operationId"] = oid
            if op.get("description") and len(op["description"]) > 300:
                op["description"] = op["description"][:297] + "..."
    return spec
