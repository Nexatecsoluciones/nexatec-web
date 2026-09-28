import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.routers import auth, demos, password_reset, portal, system_access, systems, tenants

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
