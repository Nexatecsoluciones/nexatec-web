"""Resuelve conexiones a bases de tenant a partir del registry
(TenantDatabase), nunca de un host/puerto asumido en el codigo de negocio.

No mantiene un pool por cada tenant existente: crea el engine de forma
perezosa (lazy) la primera vez que se necesita, y acota cuantos quedan
abiertos en memoria (LRU simple). Con pocos tenants esto es mas
infraestructura de la que hace falta hoy, pero se documenta y se deja
lista porque el models/tenancy.py y el registry ya asumen que puede haber
muchos: dejarlo para "cuando haya mas tenants" significaria reescribir el
punto de acceso a datos de cada producto mas adelante.

Estrategia futura si el numero de tenants crece mucho: mover a PgBouncer
en modo transaction pooling en vez de pools de aplicacion por tenant (ver
docs/ARCHITECTURE.md)."""

import logging
import threading
import time
from collections import OrderedDict

from sqlalchemy import Engine, create_engine

from app.core.crypto import decrypt_secret
from app.models.tenancy import TenantDatabase, TenantDatabaseCredential
from app.models.tenancy_enums import ProvisioningStatus

logger = logging.getLogger("nexatec.tenant_db_manager")

MAX_CACHED_ENGINES = 20
IDLE_TTL_SECONDS = 15 * 60


class TenantDatabaseUnavailable(Exception):
    pass


class TenantDatabaseManager:
    def __init__(self, max_cached: int = MAX_CACHED_ENGINES, idle_ttl_seconds: int = IDLE_TTL_SECONDS):
        self._max_cached = max_cached
        self._idle_ttl_seconds = idle_ttl_seconds
        self._engines: OrderedDict[str, tuple[Engine, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get_engine(self, tenant_database: TenantDatabase, credential: TenantDatabaseCredential) -> Engine:
        if tenant_database.status != ProvisioningStatus.READY:
            raise TenantDatabaseUnavailable(
                f"La base del tenant no esta lista (status={tenant_database.status.value})."
            )

        key = str(tenant_database.id)
        with self._lock:
            cached = self._engines.get(key)
            if cached is not None:
                engine, _ = cached
                self._engines.move_to_end(key)
                self._engines[key] = (engine, time.monotonic())
                return engine

            self._evict_if_needed_locked()

            password = decrypt_secret(credential.encrypted_password)
            url = (
                f"postgresql+psycopg://{tenant_database.database_user_ref}:{password}"
                f"@127.0.0.1:5432/{tenant_database.database_name}"
            )
            engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=1)
            del password  # no queda referencia viva mas de lo necesario

            self._engines[key] = (engine, time.monotonic())
            logger.info("tenant_engine_opened tenant_database_id=%s", key)
            return engine

    def _evict_if_needed_locked(self) -> None:
        while len(self._engines) >= self._max_cached:
            old_key, (old_engine, _) = self._engines.popitem(last=False)
            old_engine.dispose()
            logger.info("tenant_engine_evicted tenant_database_id=%s", old_key)

    def close_idle(self) -> int:
        """Cierra engines sin uso hace mas de idle_ttl_seconds. Pensado para
        llamarse periodicamente (worker/cron); no se agenda todavia en esta
        fase por no tener aun un scheduler de background jobs."""
        closed = 0
        now = time.monotonic()
        with self._lock:
            for key in list(self._engines.keys()):
                engine, last_used = self._engines[key]
                if now - last_used > self._idle_ttl_seconds:
                    engine.dispose()
                    del self._engines[key]
                    closed += 1
                    logger.info("tenant_engine_closed_idle tenant_database_id=%s", key)
        return closed

    def dispose_all(self) -> None:
        with self._lock:
            for engine, _ in self._engines.values():
                engine.dispose()
            self._engines.clear()


tenant_db_manager = TenantDatabaseManager()
