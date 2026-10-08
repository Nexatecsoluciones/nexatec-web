"""La base del control plane migrada con Alembic coincide con los modelos
(detecta migraciones escritas a mano que se desvian de app/models)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import app.models.control_center  # noqa: F401
import app.models.control_plane  # noqa: F401
import app.models.media  # noqa: F401
import app.models.payments  # noqa: F401
import app.models.site_content  # noqa: F401
import app.models.system  # noqa: F401
import app.models.tenancy  # noqa: F401
from app.core.db import Base, engine


def test_control_plane_has_no_drift():
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff
