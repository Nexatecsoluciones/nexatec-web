"""Anti-SSRF para app/models/control_center.py::ServiceRegistry. Solo
SUPER_ADMIN puede escribir un `internal_target`, pero ni asi se acepta
cualquier cosa: se valida contra un allow-list explicito de puertos
internos conocidos de este servidor, o -- para servicios externos
legitimos -- se exige HTTPS y se rechazan rangos de IP privados/locales
(incluido el endpoint de metadata de nubes, 169.254.169.254) para que un
admin no pueda (por error o por una cuenta comprometida) convertir el
health center en una forma de golpear la red interna."""

import ipaddress
import socket
from urllib.parse import urlparse

from app.models.control_center_enums import ServiceType

# Puertos de servicios internos propios que YA conocemos y administramos
# nosotros (ver docs/DEPLOYMENT.md). Un ServiceRegistry INTERNAL_SERVICE/
# MANAGED_PRODUCT solo puede apuntar a 127.0.0.1 en uno de estos puertos,
# nunca a un host ni puerto arbitrario.
ALLOWED_INTERNAL_PORTS = {4301, 4302, 3900, 3901, 5432}

_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}


class ServiceTargetError(ValueError):
    pass


def _is_private_or_reserved(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # no se pudo parsear: tratar como no seguro
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


def validate_internal_target(service_type: ServiceType, target: str) -> None:
    if service_type in (ServiceType.INTERNAL_SERVICE, ServiceType.MANAGED_PRODUCT):
        host, _, port_str = target.partition(":")
        if host != "127.0.0.1":
            raise ServiceTargetError(
                "Los servicios internos/managed solo pueden apuntar a 127.0.0.1 -- "
                "nunca a un host arbitrario."
            )
        try:
            port = int(port_str)
        except ValueError:
            raise ServiceTargetError("Formato invalido, se espera '127.0.0.1:<puerto>'.")
        if port not in ALLOWED_INTERNAL_PORTS:
            raise ServiceTargetError(
                f"Puerto {port} no esta en la lista de puertos internos permitidos "
                f"({sorted(ALLOWED_INTERNAL_PORTS)}). Agregarlo requiere revisión explícita."
            )
        return

    # EXTERNAL_SERVICE: exige HTTPS y bloquea rangos privados/metadata.
    parsed = urlparse(target)
    if parsed.scheme != "https":
        raise ServiceTargetError("Los servicios externos deben usar HTTPS.")
    if not parsed.hostname:
        raise ServiceTargetError("URL invalida: falta el host.")
    if parsed.hostname in _BLOCKED_HOSTS:
        raise ServiceTargetError("Ese host esta bloqueado explicitamente (endpoint de metadata de nube).")

    try:
        resolved_ips = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror:
        raise ServiceTargetError("No se pudo resolver el host.")

    for ip in resolved_ips:
        if _is_private_or_reserved(ip):
            raise ServiceTargetError(
                f"El host resuelve a una IP privada/reservada ({ip}) -- no permitido para servicios externos."
            )
