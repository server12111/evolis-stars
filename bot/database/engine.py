from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bot.config import get_settings

settings = get_settings()

_engine_kwargs = {"echo": False, "pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"timeout": 30}

engine = create_async_engine(settings.database_url, **_engine_kwargs)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from bot.database import models  # noqa: F401 — import to register all models
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not alter an existing SQLite database. Keep startup
        # backward-compatible for installations created before later columns
        # were added.
        if settings.database_url.startswith("sqlite"):
            await conn.run_sync(_add_missing_user_columns)


def _add_missing_user_columns(connection) -> None:
    columns = {column["name"] for column in inspect(connection).get_columns("users")}
    additions = {
        "referral_counted": "BOOLEAN NOT NULL DEFAULT 0",
        "sponsor_wave": "INTEGER NOT NULL DEFAULT 0",
        "sponsor_wave_one": "TEXT",
        "sponsor_wave_two": "TEXT",
        "is_vip": "BOOLEAN NOT NULL DEFAULT 0",
        "referral_insufficient_notified": "BOOLEAN NOT NULL DEFAULT 0",
    }
    referral_counted_added = "referral_counted" not in columns
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
    if referral_counted_added:
        # Before this migration referrals_count was incremented on /start.
        # Preserve that historical state and avoid double-counting users.
        connection.execute(text(
            "UPDATE users SET referral_counted = 1 WHERE referrer_id IS NOT NULL"
        ))
    task_columns = {
        column["name"] for column in inspect(connection).get_columns("tasks")
    }
    if "photo_file_id" not in task_columns:
        connection.execute(
            text("ALTER TABLE tasks ADD COLUMN photo_file_id VARCHAR(256)")
        )
    _ensure_integrity_indexes(connection)


def _ensure_integrity_indexes(connection) -> None:
    indexes = (
        ("task_completions", "task_id, user_id", "uq_task_completion_task_user"),
        ("promo_uses", "code_id, user_id", "uq_promo_use_code_user"),
    )
    for table, columns, index_name in indexes:
        duplicate = connection.execute(text(
            f"SELECT 1 FROM {table} GROUP BY {columns} HAVING COUNT(*) > 1 LIMIT 1"
        )).first()
        if duplicate is None:
            connection.execute(text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})"
            ))


async def get_session() -> AsyncSession:
    async with SessionFactory() as session:
        yield session
