import enum


class Role(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    SUPPORT = "SUPPORT"
    BILLING = "BILLING"
    CLIENT_ADMIN = "CLIENT_ADMIN"
    CLIENT_USER = "CLIENT_USER"
    DEMO_USER = "DEMO_USER"


# Roles con acceso al panel /admin. Todo lo demas es portal de cliente.
ADMIN_ROLES = {Role.SUPER_ADMIN, Role.ADMIN, Role.SUPPORT, Role.BILLING}
