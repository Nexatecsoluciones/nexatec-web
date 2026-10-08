import httpx

from app.core.config import get_settings

settings = get_settings()

_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Claves de prueba publicas de Cloudflare (documentadas): siempre aprueban o
# siempre rechazan. Sirven para staging, NUNCA protegen de bots.
_TEST_SECRET_PREFIXES = ("1x0000000000000000000000000000000", "2x0000000000000000000000000000000",
                         "3x0000000000000000000000000000000")


def turnstile_mode() -> str:
    """"real", "test" (claves de prueba: sin proteccion) o "off"."""
    secret = settings.turnstile_secret_key
    if not secret:
        return "off"
    return "test" if secret.startswith(_TEST_SECRET_PREFIXES) else "real"


async def verify_turnstile_token(token: str, remote_ip: str | None) -> bool:
    """Valida el token de Turnstile SIEMPRE server-side. Si no hay claves
    configuradas todavia, el bypass depende EXCLUSIVAMENTE de
    NEXATEC_ENV=development -- nunca de "no produccion" (staging, test,
    o cualquier valor futuro deben exigir Turnstile real). Nadie puede
    activar este bypass modificando una peticion publica: es una
    variable de entorno server-side, no algo que llegue en el request."""
    if not settings.turnstile_secret_key:
        return settings.is_development

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
