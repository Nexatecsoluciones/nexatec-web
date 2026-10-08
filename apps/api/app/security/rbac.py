from fastapi import Depends, HTTPException, status

from app.core.config import get_settings

from app.security.roles import ADMIN_ROLES, Role
from app.security.session_auth import CurrentUser, get_current_user

__all__ = ["Role", "ADMIN_ROLES", "require_roles", "require_admin_panel", "require_tenant_ownership"]


def require_roles(*allowed: Role):
    """Dependencia de FastAPI: exige que el usuario autenticado tenga uno de
    los roles permitidos. La autorizacion se resuelve siempre server-side;
    el frontend nunca decide esto."""

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para esta accion.",
            )
        # El personal de NEXATEC no opera el Control Center sin MFA. El
        # detalle es un codigo estable para que la interfaz lleve a la
        # pantalla de configuracion.
        if user.role in ADMIN_ROLES and get_settings().require_admin_mfa and not user.mfa_enabled:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA_REQUIRED")
        return user

    return _check


def require_admin_panel():
    return require_roles(*ADMIN_ROLES)


def require_tenant_ownership(resource_tenant_id, user: CurrentUser) -> None:
    """Bloquea IDOR entre tenants: un CLIENT_ADMIN/CLIENT_USER/DEMO_USER solo
    puede operar sobre recursos de su propio tenant, sin importar lo que
    diga la URL o el body de la request."""
    if user.role in ADMIN_ROLES:
        return
    if user.tenant_id != resource_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recurso no encontrado.",
        )
