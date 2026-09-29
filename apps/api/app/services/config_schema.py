"""Motor de configuracion no-code. Un `config_schema`/`modules_schema` es
SIEMPRE una lista de definiciones de campo con una forma fija y
validada -- nunca codigo. No hay eval(), no hay JS/Python/shell
arbitrario: un campo solo puede ser uno de los tipos declarados aca, y
`validate_schema`/`validate_config_against_schema` rechazan cualquier
otra cosa antes de que llegue a guardarse.

Forma de un field definition (lo unico que el schema puede describir):

{
  "key": "moneda",          # identificador, [a-z0-9_]+
  "label": "Moneda",
  "type": "select",          # text | select | boolean | number | media | secret
  "required": true,
  "options": ["PYG", "USD"],  # solo para type=select
  "default": "PYG"
}
"""

import re

ALLOWED_FIELD_TYPES = {"text", "select", "boolean", "number", "media", "secret"}
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
MAX_FIELDS_PER_SCHEMA = 60


class SchemaValidationError(ValueError):
    pass


def validate_schema(schema: list | None) -> list:
    """Valida la ESTRUCTURA del schema (lo que el Product Studio guarda),
    no valores de configuracion concretos. Lanza SchemaValidationError con
    un mensaje especifico ante cualquier forma no reconocida -- nunca deja
    pasar un campo de un tipo no declarado en ALLOWED_FIELD_TYPES."""
    if schema is None:
        return []
    if not isinstance(schema, list):
        raise SchemaValidationError("El schema debe ser una lista de campos.")
    if len(schema) > MAX_FIELDS_PER_SCHEMA:
        raise SchemaValidationError(f"Maximo {MAX_FIELDS_PER_SCHEMA} campos por schema.")

    seen_keys: set[str] = set()
    for i, field in enumerate(schema):
        if not isinstance(field, dict):
            raise SchemaValidationError(f"Campo #{i}: debe ser un objeto.")

        key = field.get("key")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise SchemaValidationError(f"Campo #{i}: 'key' invalida (solo a-z0-9_, empieza con letra).")
        if key in seen_keys:
            raise SchemaValidationError(f"Campo #{i}: key '{key}' duplicada.")
        seen_keys.add(key)

        field_type = field.get("type")
        if field_type not in ALLOWED_FIELD_TYPES:
            raise SchemaValidationError(
                f"Campo '{key}': tipo '{field_type}' no permitido. Debe ser uno de {sorted(ALLOWED_FIELD_TYPES)}."
            )

        label = field.get("label")
        if not isinstance(label, str) or not label.strip():
            raise SchemaValidationError(f"Campo '{key}': 'label' requerido.")

        if field_type == "select":
            options = field.get("options")
            if not isinstance(options, list) or not options or not all(isinstance(o, str) for o in options):
                raise SchemaValidationError(f"Campo '{key}': 'options' debe ser una lista de strings no vacia.")

    return schema


def validate_config_against_schema(schema: list, config: dict) -> dict:
    """Valida VALORES concretos contra un schema ya validado. Campos no
    declarados en el schema se descartan silenciosamente (no se guardan) --
    nunca se persiste una clave arbitraria que el cliente haya inventado."""
    if not isinstance(config, dict):
        raise SchemaValidationError("La configuracion debe ser un objeto.")

    schema_by_key = {f["key"]: f for f in schema}
    cleaned: dict = {}

    for key, field in schema_by_key.items():
        if key not in config:
            if field.get("required"):
                raise SchemaValidationError(f"Falta el campo requerido '{key}'.")
            continue

        value = config[key]
        field_type = field["type"]

        if field_type == "boolean":
            if not isinstance(value, bool):
                raise SchemaValidationError(f"'{key}' debe ser booleano.")
        elif field_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SchemaValidationError(f"'{key}' debe ser numerico.")
        elif field_type == "select":
            if value not in field["options"]:
                raise SchemaValidationError(f"'{key}': valor '{value}' no esta en las opciones permitidas.")
        elif field_type in ("text", "media"):
            if not isinstance(value, str) or len(value) > 2000:
                raise SchemaValidationError(f"'{key}' debe ser texto (maximo 2000 caracteres).")
        elif field_type == "secret":
            # Los valores secret NUNCA pasan por aca -- se escriben via un
            # endpoint separado que los cifra y jamas los devuelve. Si
            # llega uno en el config normal, se rechaza explicitamente.
            raise SchemaValidationError(
                f"'{key}' es un campo secret: usar el endpoint de secrets, no la configuracion normal."
            )

        cleaned[key] = value

    return cleaned


def validate_modules_against_schema(modules_schema: list, modules: dict) -> dict:
    """Los modulos son simples booleanos on/off, definidos por key en
    modules_schema (misma validacion de forma que un campo boolean)."""
    allowed_keys = {m["key"] for m in modules_schema}
    if not isinstance(modules, dict):
        raise SchemaValidationError("Los modulos deben ser un objeto {key: bool}.")
    cleaned = {}
    for key, value in modules.items():
        if key not in allowed_keys:
            continue  # se descarta, no se guarda una key que el producto no define
        if not isinstance(value, bool):
            raise SchemaValidationError(f"Modulo '{key}' debe ser booleano.")
        cleaned[key] = value
    return cleaned
