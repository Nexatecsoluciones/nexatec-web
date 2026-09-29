import enum


class ServiceType(str, enum.Enum):
    INTERNAL_SERVICE = "INTERNAL_SERVICE"
    EXTERNAL_SERVICE = "EXTERNAL_SERVICE"
    MANAGED_PRODUCT = "MANAGED_PRODUCT"


class ServiceHealthStatus(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class DemoRequestStatus(str, enum.Enum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PROVISIONED = "PROVISIONED"


class JobType(str, enum.Enum):
    DEMO_PROVISION = "DEMO_PROVISION"
    PRODUCTION_PROVISION = "PRODUCTION_PROVISION"
    DEMO_TO_PRODUCTION = "DEMO_TO_PRODUCTION"


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
