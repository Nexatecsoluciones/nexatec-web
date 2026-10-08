import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.routers import (
    admin_users,
    auth,
    config_center,
    dashboard,
    demo_requests,
    demos,
    erp_accounting,
    erp_crm,
    erp_dashboard,
    erp_hr,
    erp_inventory,
    erp_masters,
    erp_purchases,
    erp_receivables,
    erp_sales,
    jobs,
    media,
    password_reset,
    payments,
    portal,
    public_hostname,
    site_content,
    service_registry,
    system_access,
    system_status,
    systems,
    tenants,
)
from app.routers import health as health_router

settings = get_settings()

app = FastAPI(title="NEXATEC API", docs_url=None if settings.is_production else "/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def security_headers_and_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Nunca exponer stack traces al cliente. El detalle queda solo en logs
    # internos (stdout -> journal/systemd en este servidor), correlacionado
    # por request_id.
    request_id = getattr(request.state, "request_id", "unknown")
    print(f"[error_id={request_id}] {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno.", "error_id": request_id},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/ready")
async def ready():
    from sqlalchemy import text

    from app.core.db import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})


app.include_router(auth.router)
app.include_router(password_reset.router)
app.include_router(systems.router)
app.include_router(systems.admin_router)
app.include_router(tenants.router)
app.include_router(system_access.router)
app.include_router(demos.router)
app.include_router(portal.router)
app.include_router(public_hostname.router)
app.include_router(site_content.public_router)
app.include_router(site_content.admin_router)
app.include_router(erp_masters.router)
app.include_router(erp_inventory.router)
app.include_router(erp_sales.router)
app.include_router(erp_receivables.router)
app.include_router(erp_purchases.router)
app.include_router(erp_crm.router)
app.include_router(erp_hr.router)
app.include_router(erp_accounting.router)
app.include_router(erp_dashboard.router)
app.include_router(media.router)
app.include_router(payments.router)
app.include_router(system_status.router)
app.include_router(config_center.router)
app.include_router(service_registry.router)
app.include_router(demo_requests.router)
app.include_router(jobs.router)
app.include_router(dashboard.router)
app.include_router(health_router.router)
app.include_router(admin_users.router)
