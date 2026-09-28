import ipaddress


def safe_ip(value: str | None) -> str | None:
    """Devuelve la IP solo si es valida; si no (proxy mal configurado,
    entorno de test, header spoofeado), devuelve None en vez de fallar
    la insercion en una columna INET o loggear basura."""
    if not value:
        return None
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        return None
