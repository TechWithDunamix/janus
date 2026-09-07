"""Database wiring.

One definition of how Janus connects, shared by the running application, the
migration commands, the CLI and the test harness — so the application and its
migrations cannot drift apart.
"""

from __future__ import annotations

from sillo.record import DatabaseConfig, DatabaseManager

from app.config import config

#: Modules scanned for models.
#:
#: `sillo.permissions.models` is here because Janus's RBAC *is* the framework's
#: — `Permission`, `Group`, `UserGroup`, `GroupPermission` — and those tables
#: have to be created. `sillo.users` is deliberately not: Janus's own `User`
#: already claims `table = "users"`, and registering the framework's would give
#: two models the same table.
MODEL_MODULES = ["database.models", "sillo.permissions.models"]

#: Where migrations live, as a dotted path.
MIGRATIONS_MODULE = "database.migrations"


def database_config(*, generate_schemas: bool | None = None) -> DatabaseConfig:
    """The connection settings.

    Args:
        generate_schemas: Whether this process creates missing tables. Defaults
            to `DB_GENERATE_SCHEMAS`. A worker passes `False` — see
            :func:`database`.
    """
    return DatabaseConfig(
        url=config.database_url,
        pool_size=config.db_pool_size,
        echo=config.db_echo,
        generate_schemas=(
            config.db_generate_schemas if generate_schemas is None else generate_schemas
        ),
    )


def database(*, generate_schemas: bool | None = None) -> DatabaseManager:
    """A manager for scripts that need the ORM outside a request::

        async with database() as db:
            await Gateway.all()

    The application does not call this — `setup_record` in `app/bootstrap.py`
    builds its own from the same settings and ties it to startup and shutdown.

    Args:
        generate_schemas: Pass `False` from a process that does not own the
            schema. Creating tables concurrently is not safe: two processes
            starting together race on the implicit row type behind
            `CREATE TABLE IF NOT EXISTS` and fail with an error that names
            nothing about the race. The web process owns the schema; workers
            and the scheduler wait for it.
    """
    manager = DatabaseManager(database_config(generate_schemas=generate_schemas))
    manager.register_models(*MODEL_MODULES).set_migrations(MIGRATIONS_MODULE)
    return manager
