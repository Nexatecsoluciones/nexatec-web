import enum


class TenantStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class TenantMemberRole(str, enum.Enum):
    """Rol DENTRO de un tenant. No confundir con el Role global (Role en
    app/security/roles.py) que aplica a personal de NEXATEC sin tenant."""

    CLIENT_ADMIN = "CLIENT_ADMIN"
    # Solo lectura de todo (nombre historico).
    CLIENT_USER = "CLIENT_USER"
    # Roles por funcion dentro de la empresa. La matriz de permisos esta en
    # app/security/erp_permissions.py.
    MANAGER = "MANAGER"
    FINANCE = "FINANCE"
    SALES = "SALES"
    PURCHASING = "PURCHASING"
    WAREHOUSE = "WAREHOUSE"
    ACCOUNTANT = "ACCOUNTANT"
    AUDITOR = "AUDITOR"


class TenantMemberStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"
    REMOVED = "REMOVED"


class Environment(str, enum.Enum):
    DEMO = "DEMO"
    PRODUCTION = "PRODUCTION"


class SystemAccessStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ProvisioningStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    FAILED = "FAILED"
    DEPROVISIONING = "DEPROVISIONING"
    DEPROVISIONED = "DEPROVISIONED"
