"""Local commands: migrate, seed, serve, worker, scheduler, work, collect.

These need the database or the process, not the API. None of them performs an
operation a signed-in user could perform through the dashboard, so none of them
is a way around authorization — see `app/cli/base.py` for the split.
"""

from __future__ import annotations

import asyncio

from sillo.console import Command, Flag, Option

from app.cli.base import LocalCommand

__all__ = ["Collect", "Migrate", "Scheduler", "Seed", "Serve", "Work", "Worker"]


class Migrate(LocalCommand):
    """Bring the schema up to date.

    `generate_schemas` creates missing *tables* and stops there — a column
    added to a model after its table exists is silently absent, and the first
    query that reads it fails with `no such column` at runtime rather than
    here. So this also reconciles added columns, which is what a model edit
    produces most of the time.

    What it deliberately does not do is drop or narrow an existing column.
    Removing one loses data and narrowing one can fail halfway; both deserve a
    written migration rather than a command that runs on every deploy.
    """

    name = "migrate"
    help = "Create missing tables and columns, and seed the permission catalogue."

    async def run_async(self) -> int:
        from tortoise import Tortoise

        from app.authz import ensure_roles

        await Tortoise.generate_schemas(safe=True)
        added = await self._reconcile_columns()
        roles = await ensure_roles()

        if self.wants_json:
            self.emit({"ok": True, "columns_added": added, "roles": sorted(roles)})
            return 0

        self.success("Schema is up to date.")
        for column in added:
            self.bullet(f"added {column}")
        self.muted(f"Roles: {', '.join(sorted(roles))}")
        return 0

    async def _reconcile_columns(self) -> list[str]:
        """Add columns a model declares and its table does not have."""
        from tortoise import Tortoise

        connection = Tortoise.get_connection("default")
        dialect = connection.capabilities.dialect
        added: list[str] = []

        # `Tortoise.apps` is an `Apps` mapping, not a dict — it supports
        # subscripting but not `.get`.
        for model in dict(Tortoise.apps["models"]).values():
            table = model._meta.db_table
            if not await self._table_exists(connection, table, dialect):
                continue
            existing = await self._columns(connection, table, dialect)

            for name, field in model._meta.fields_map.items():
                column = getattr(field, "source_field", None) or name
                if column in existing or not getattr(field, "has_db_field", True):
                    continue
                sql_type = self._sql_type(field)
                if sql_type is None:
                    continue
                await connection.execute_query(
                    f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                )
                added.append(f"{table}.{column}")

        return added

    @staticmethod
    async def _table_exists(connection, table: str, dialect: str) -> bool:
        if dialect == "sqlite":
            rows = await connection.execute_query_dict(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", [table]
            )
        else:
            rows = await connection.execute_query_dict(
                "SELECT table_name AS name FROM information_schema.tables "
                "WHERE table_name = $1",
                [table],
            )
        return bool(rows)

    @staticmethod
    async def _columns(connection, table: str, dialect: str) -> set[str]:
        if dialect == "sqlite":
            rows = await connection.execute_query_dict(f"PRAGMA table_info({table})")
            return {row["name"] for row in rows}
        rows = await connection.execute_query_dict(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name = $1",
            [table],
        )
        return {row["name"] for row in rows}

    @staticmethod
    def _sql_type(field) -> str | None:
        """The column type for a field, or `None` to leave it to a migration.

        Only the shapes that can be added to a populated table safely: a
        nullable column, or one with a constant default. A `NOT NULL` with no
        default, or anything with a constraint, is deliberately skipped —
        guessing wrong here corrupts a schema rather than failing to update one.
        """
        from tortoise import fields as tf

        if getattr(field, "pk", False):
            return None
        if not getattr(field, "null", False) and field.default is None:
            return None

        if isinstance(field, tf.CharField):
            base = f"VARCHAR({getattr(field, 'max_length', 255)})"
        elif isinstance(field, tf.BooleanField):
            base = "BOOLEAN"
        elif isinstance(field, tf.BigIntField):
            base = "BIGINT"
        elif isinstance(field, tf.IntField):
            base = "INTEGER"
        elif isinstance(field, tf.FloatField):
            base = "DOUBLE PRECISION"
        elif isinstance(field, tf.DatetimeField):
            base = "TIMESTAMP"
        elif isinstance(field, tf.DateField):
            base = "DATE"
        elif isinstance(field, tf.JSONField | tf.TextField):
            base = "TEXT"
        else:
            return None

        if field.default is not None and not callable(field.default):
            if isinstance(field.default, bool):
                literal = "1" if field.default else "0"
            elif isinstance(field.default, str):
                literal = f"'{field.default}'"
            else:
                literal = str(field.default)
            return f"{base} DEFAULT {literal}"
        return base


class Seed(LocalCommand):
    name = "seed"
    help = "Create the demo estate and simulated traffic."
    arguments = [
        Flag("fresh", help="Delete existing demo data first."),
        Option("hours", default="48", help="How many hours of traffic to generate."),
    ]

    async def run_async(self) -> int:
        from database.seeds import seed

        if self.flag("fresh") and not self.flag("yes"):
            from sillo.console.terminal import is_interactive

            if is_interactive() and not self.confirm(
                "Delete all gateway, traffic and audit data first?", default=False
            ):
                self.muted("Cancelled.")
                return 1

        result = await seed(fresh=self.flag("fresh"), hours=int(self.option("hours")))
        if self.wants_json:
            self.emit(result)
            return 0

        self.success("Demo estate created.")
        self.pairs([[k.replace("_", " ").title(), v] for k, v in result.items()])
        self.blank()
        # Said plainly, every time. The whole point of the flag on those rows is
        # that nobody mistakes generated traffic for observation.
        self.warn("All generated traffic is marked as simulated and is labelled as such in the UI.")
        self.blank()
        self.muted("Sign in with owner@janus.local / janus-development-owner")
        return 0


class Work(LocalCommand):
    name = "work"
    help = "Run every scheduled job once."

    async def run_async(self) -> int:
        from app.jobs import run_all_once
        from app.queue import bind_jobs

        bind_jobs()
        results = await run_all_once()
        if self.wants_json:
            self.emit(results)
            return 0
        for name, outcome in results.items():
            if isinstance(outcome, dict) and "error" in outcome:
                self.error(f"{name}: {outcome['error']}")
            else:
                self.success(f"{name}: {outcome}")
        return 0


class Collect(LocalCommand):
    name = "collect"
    help = "Ingest new access-log lines now."

    async def run_async(self) -> int:
        from app.collector import ingest_file, load_state, save_state
        from database.models import Gateway

        total = 0
        for gateway in await Gateway.filter(enabled=True):
            state = load_state(gateway, gateway.access_log_path)
            state = await ingest_file(gateway, state)
            save_state(gateway, state)
            total += state.ingested
            if not self.wants_json:
                for warning in state.warnings:
                    self.warn(f"{gateway.name}: {warning}")
                self.success(
                    f"{gateway.name}: {state.ingested} ingested, {state.skipped} skipped."
                )
        if self.wants_json:
            self.emit({"ingested": total})
        return 0


class Serve(Command):
    name = "serve"
    help = "Run the Janus control plane."
    arguments = [
        Option("host", default="127.0.0.1"),
        Option("port", default="8000"),
        Flag("reload", help="Restart on code changes."),
    ]

    def handle(self) -> int:
        try:
            import uvicorn
        except ImportError:
            self.error("uvicorn is not installed. Install with: pip install 'janus[server]'")
            return 1

        uvicorn.run(
            "app.main:app",
            host=self.option("host"),
            port=int(self.option("port")),
            reload=self.flag("reload"),
        )
        return 0


class Worker(Command):
    name = "worker"
    help = "Drain the job queues."
    arguments = [
        Option("queues", default="collector,health,gateway,analytics"),
        Option("concurrency", default="4"),
    ]

    def handle(self) -> int:
        async def main() -> int:
            from database.config import database

            # `generate_schemas=False`: the web process owns the schema.
            # Creating tables concurrently races on the implicit row type behind
            # `CREATE TABLE IF NOT EXISTS`, and the error names nothing about
            # the race that caused it.
            async with database(generate_schemas=False):
                from sillo.work.queue.workers import Worker as QueueWorker

                from app.queue import bind_jobs, connection

                bind_jobs()
                queues = [q.strip() for q in self.option("queues").split(",") if q.strip()]
                self.info(f"Draining {', '.join(queues)}…")
                worker = QueueWorker(
                    connection(), queues=queues, concurrency=int(self.option("concurrency"))
                )
                await worker.run()
            return 0

        try:
            return asyncio.run(main())
        except KeyboardInterrupt:
            return 0


class Scheduler(Command):
    name = "scheduler"
    help = "Run the periodic jobs on their schedule."

    def handle(self) -> int:
        async def main() -> int:
            from database.config import database

            async with database(generate_schemas=False):
                from app.jobs import SCHEDULE
                from app.queue import bind_jobs

                bind_jobs()
                self.info(f"Scheduling {len(SCHEDULE)} jobs.")
                # A plain loop rather than a cron library. Every interval here
                # is "every N seconds" — none is calendar-shaped — and a
                # dependency that parses crontab syntax to do that would be a
                # dependency earning nothing.
                last: dict[str, float] = {}
                loop = asyncio.get_running_loop()
                while True:
                    now = loop.time()
                    for job_class, interval in SCHEDULE:
                        key = job_class.__name__
                        if now - last.get(key, 0) >= interval:
                            last[key] = now
                            try:
                                await job_class().handle()
                            except Exception as error:  # noqa: BLE001
                                self.error(f"{key}: {error}")
                    await asyncio.sleep(5)

        try:
            return asyncio.run(main())
        except KeyboardInterrupt:
            return 0
