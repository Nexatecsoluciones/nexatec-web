"""Generacion segura de nombres de base de datos/rol por tenant+sistema
+entorno. Regla de oro: el nombre real de una DB o ROL de PostgreSQL nunca
se construye a partir de texto libre suministrado por un usuario (nombre de
tenant, slug editable, etc.) -- se deriva unicamente de los bytes hex de
UUIDs ya validados por la base de datos, que por construccion solo pueden
contener [0-9a-f]. Esto elimina la superficie de inyeccion en el nombre
mismo (no reemplaza el uso de identificadores parametrizados/quoted al
ejecutar el DDL, ver app/services/provisioning.py).
"""

import re
import uuid

from app.models.tenancy_enums import Environment

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_ENV_SUFFIX = {Environment.DEMO: "demo", Environment.PRODUCTION: "prod"}


def sanitize_identifier_component(raw: str, max_len: int = 20) -> str:
    """Defensa en profundidad para cualquier lugar que en el futuro quiera
    derivar un identificador de texto libre (hoy el flujo de provisioning
    NO la usa para el nombre de DB, ver build_tenant_database_name). Nunca
    deja pasar nada fuera de [a-z0-9_]."""
    lowered = raw.strip().lower()
    # Cualquier caracter no [a-z0-9] (unicode, espacios, comillas, ';',
    # '/', etc.) se descarta explicitamente en vez de intentar transliterar.
    cleaned = re.sub(r"[^a-z0-9]+", "_", lowered)
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        # Nombre vacio tras sanitizar (p.ej. era solo simbolos/unicode) o
        # empieza con digito: PostgreSQL exige que un identificador sin
        # comillas empiece con letra.
        cleaned = f"x{cleaned}" if cleaned else "x"
    return cleaned[:max_len]


def build_tenant_database_identifier(
    tenant_id: uuid.UUID, system_id: uuid.UUID, environment: Environment
) -> str:
    """nxt_<8 hex del tenant>_<8 hex del sistema>_<demo|prod>. Determinista,
    <=63 bytes (limite de PostgreSQL), y seguro por construccion: hex(UUID)
    solo puede contener [0-9a-f]."""
    tenant_part = tenant_id.hex[:8]
    system_part = system_id.hex[:8]
    identifier = f"nxt_{tenant_part}_{system_part}_{_ENV_SUFFIX[environment]}"

    if not _SAFE_IDENTIFIER_RE.match(identifier):
        # No deberia poder pasar nunca (hex + literales fijos), pero se
        # valida explicitamente antes de que este valor llegue a tocar DDL.
        raise ValueError(f"Identificador generado no es seguro: {identifier!r}")

    return identifier


def assert_safe_identifier(identifier: str) -> None:
    """Ultima barrera antes de usar un identificador en DDL. Se llama
    incluso sobre valores ya generados por build_tenant_database_identifier
    (defensa en profundidad, coste despreciable)."""
    if not _SAFE_IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Identificador no seguro rechazado: {identifier!r}")
