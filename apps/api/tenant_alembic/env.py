"""Alembic de las bases de TENANT (no del control plane -- ese es alembic/).

Nunca tiene una URL fija: la conexion la pasa app/services/tenant_migrations.py
via `config.attributes["connection"]` (ya autenticada con el rol propio de
ese tenant). Para generar migraciones nuevas en desarrollo, se puede pasar
una URL de una base descartable con `-x url=postgresql+psycopg://...`."""

from alembic import context
from sqlalchemy import create_engine, pool

from app.tenant_models import TenantBase
from app.tenant_models import accounting, core, crm, hr, inventory, purchases, receivables, sales  # noqa: F401  (registra los modelos en TenantBase.metadata)

config = context.config
target_metadata = TenantBase.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return

    url = context.get_x_argument(as_dictionary=True).get("url")
    if not url:
        raise RuntimeError(
            "tenant_alembic necesita una conexion (via tenant_migrations) o -x url=... "
            "apuntando a una base DESCARTABLE. Nunca correr esto a mano contra una base de cliente."
        )
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    raise RuntimeError("tenant_alembic no soporta modo offline.")
run_migrations_online()
