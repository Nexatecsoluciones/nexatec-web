import httpx

from app.core.config import get_settings

settings = get_settings()

_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile_token(token: str, remote_ip: str | None) -> bool:
    """Valida el token de Turnstile SIEMPRE server-side. Si no hay claves
    configuradas (FASE 7 pendiente), se rechaza en produccion y se deja
    pasar solo en development para no bloquear el desarrollo local."""
    if not settings.turnstile_secret_key:
        if settings.is_production:
            return False
        return True

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            _VERIFY_URL,
            data={
                "secret": settings.turnstile_secret_key,
                "response": token,
                "remoteip": remote_ip or "",
            },
        )
        data = resp.json()
        return bool(data.get("success"))
