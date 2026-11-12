"""Idempotent startup schema evolution for the prototype database.

There is no migration history in the original project and SQLite `create_all`
never alters an existing table. This helper adds new nullable/defaulted columns
to an existing database so a working `voicekyc.db` keeps working after an
upgrade. Deployment uses Alembic instead (see `backend/migrations/`).
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("voicekyc.migrations")

# (table, column, DDL type, default clause or None)
_COLUMNS = [
    ("applications", "reviewed_fields", "JSON", "'{}'"),
    ("applications", "assessment", "JSON", "'{}'"),
    ("conversation_messages", "client_message_id", "VARCHAR(64)", None),
]


def apply_startup_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    dialect = engine.dialect.name

    with engine.begin() as connection:
        for table, column, column_type, default in _COLUMNS:
            if table not in existing_tables:
                continue
            columns = {item["name"] for item in inspector.get_columns(table)}
            if column in columns:
                continue
            if dialect == "sqlite":
                type_sql = "TEXT" if column_type == "JSON" else column_type
            elif dialect == "postgresql":
                type_sql = "JSONB" if column_type == "JSON" else column_type
            else:
                type_sql = column_type
            ddl = f"ALTER TABLE {table} ADD COLUMN {column} {type_sql}"
            if default:
                ddl += f" DEFAULT {default}"
            connection.execute(text(ddl))
            logger.info(
                "schema.column_added",
                extra={"event": "schema.column_added", "path": f"{table}.{column}"},
                # `path` is a log extra key used by the request middleware, so
                # include the column in the message instead of overloading it.
            )
            print(f"schema: added {table}.{column}")

        if "conversation_messages" in existing_tables:
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_conversation_messages_client_message_id "
                    "ON conversation_messages (client_message_id)"
                )
            )
