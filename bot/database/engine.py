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
        "is_premium": "BOOLEAN NOT NULL DEFAULT 0",
        "free_game_credits": "INTEGER NOT NULL DEFAULT 0",
        "free_game_credit_amount": "NUMERIC(14,2)",
        "referral_insufficient_notified": "BOOLEAN NOT NULL DEFAULT 0",
        "tos_accepted": "BOOLEAN NOT NULL DEFAULT 0",
        "tos_gate_shown": "BOOLEAN NOT NULL DEFAULT 0",
        "last_random_at": "DATETIME",
        "rp_migrated": "BOOLEAN NOT NULL DEFAULT 0",
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
    # RP⭐️ currency migration: every pre-existing balance was denominated in
    # the old Telegram-Stars-equivalent unit; convert it once at 1 old ⭐ = 3
    # RP⭐️. Gated entirely by rp_migrated so this is safe to run on every
    # startup — once a row is flagged, the WHERE clause never matches it
    # again, so a restart (or running this twice in one process) can never
    # re-multiply a balance. New users are created with rp_migrated=True by
    # the model's default and so are never touched here.
    migration_result = connection.execute(text(
        "UPDATE users SET "
        "stars_balance = stars_balance * 3, "
        "free_game_credit_amount = CASE WHEN free_game_credit_amount IS NOT NULL "
        "THEN free_game_credit_amount * 3 ELSE NULL END, "
        "rp_migrated = 1 "
        "WHERE rp_migrated = 0 OR rp_migrated IS NULL"
    ))
    if migration_result.rowcount:
        # Recorded once for the admin panel's "Количество выполненных
        # миграций" stat — rowcount is 0 on every later startup (the WHERE
        # clause above already excludes migrated rows), so this only ever
        # accumulates real migrations, never re-counts the same user.
        existing_count_row = connection.execute(
            text("SELECT value FROM bot_settings WHERE key = 'rp_migration_count'")
        ).first()
        existing_count = int(existing_count_row[0]) if existing_count_row else 0
        new_count = existing_count + migration_result.rowcount
        if existing_count_row:
            connection.execute(
                text("UPDATE bot_settings SET value = :v WHERE key = 'rp_migration_count'"),
                {"v": str(new_count)},
            )
        else:
            connection.execute(
                text("INSERT INTO bot_settings (key, value) VALUES ('rp_migration_count', :v)"),
                {"v": str(new_count)},
            )
    # Per-code chat-bonus/global-promo reward amounts were also fixed in the
    # old unit at creation time — convert them once too (usage_limit/
    # used_count are untouched). Gated by a one-time global marker rather
    # than a per-row flag since these tables have no such column of their
    # own; this whole function only ever runs sequentially at startup
    # before the bot handles any request, so a plain check-then-act read is
    # safe here (no concurrent writers exist yet).
    already_migrated_bonuses = connection.execute(
        text("SELECT 1 FROM bot_settings WHERE key = 'rp_bonus_migration_done'")
    ).first()
    if already_migrated_bonuses is None:
        connection.execute(text(
            "UPDATE chat_bonus_codes SET reward_amount = reward_amount * 3, total_charged = total_charged * 3"
        ))
        connection.execute(text("UPDATE promo_codes SET reward_amount = reward_amount * 3"))
        connection.execute(text(
            "INSERT INTO bot_settings (key, value) VALUES ('rp_bonus_migration_done', '1')"
        ))
    task_columns = {
        column["name"] for column in inspect(connection).get_columns("tasks")
    }
    if "photo_file_id" not in task_columns:
        connection.execute(
            text("ALTER TABLE tasks ADD COLUMN photo_file_id VARCHAR(256)")
        )
    game_session_columns = {
        column["name"] for column in inspect(connection).get_columns("game_sessions")
    }
    if "chat_id" not in game_session_columns:
        connection.execute(
            text("ALTER TABLE game_sessions ADD COLUMN chat_id BIGINT")
        )
    if "bet_choice" not in game_session_columns:
        connection.execute(
            text("ALTER TABLE game_sessions ADD COLUMN bet_choice VARCHAR(16)")
        )
    if "result_choice" not in game_session_columns:
        connection.execute(
            text("ALTER TABLE game_sessions ADD COLUMN result_choice VARCHAR(16)")
        )
    chat_columns = {column["name"] for column in inspect(connection).get_columns("chats")}
    if "last_click_ad_posted_at" not in chat_columns:
        connection.execute(
            text("ALTER TABLE chats ADD COLUMN last_click_ad_posted_at DATETIME")
        )
        # Backfill to "now" instead of leaving it NULL: an empty column
        # would otherwise read as "never posted", so the very first pass
        # after this migration would immediately fire one spurious ad post
        # into every broadcast-enabled chat before any real cooldown
        # history exists — exactly the bug this column exists to prevent.
        connection.execute(
            text("UPDATE chats SET last_click_ad_posted_at = CURRENT_TIMESTAMP")
        )
    withdrawal_columns = {
        column["name"] for column in inspect(connection).get_columns("withdrawals")
    }
    if "recipient_username" not in withdrawal_columns:
        connection.execute(
            text("ALTER TABLE withdrawals ADD COLUMN recipient_username VARCHAR(64)")
        )
    if "withdrawal_method" not in withdrawal_columns:
        connection.execute(
            text("ALTER TABLE withdrawals ADD COLUMN withdrawal_method VARCHAR(16)")
        )
    if "rp_debited" not in withdrawal_columns:
        connection.execute(
            text("ALTER TABLE withdrawals ADD COLUMN rp_debited NUMERIC(14,2)")
        )
    if "username" not in chat_columns:
        connection.execute(
            text("ALTER TABLE chats ADD COLUMN username VARCHAR(64)")
        )
    if "invite_link" not in chat_columns:
        connection.execute(
            text("ALTER TABLE chats ADD COLUMN invite_link VARCHAR(256)")
        )
    if "custom_broadcast_enabled" not in chat_columns:
        connection.execute(
            text("ALTER TABLE chats ADD COLUMN custom_broadcast_enabled BOOLEAN DEFAULT 0")
        )
    if "custom_broadcast_interval_seconds" not in chat_columns:
        connection.execute(
            text("ALTER TABLE chats ADD COLUMN custom_broadcast_interval_seconds INTEGER")
        )
    if "custom_broadcast_last_sent_at" not in chat_columns:
        connection.execute(
            text("ALTER TABLE chats ADD COLUMN custom_broadcast_last_sent_at DATETIME")
        )
    if "custom_broadcast_next_index" not in chat_columns:
        connection.execute(
            text("ALTER TABLE chats ADD COLUMN custom_broadcast_next_index INTEGER DEFAULT 0")
        )
    broadcast_msg_columns = {
        column["name"] for column in inspect(connection).get_columns("chat_broadcast_messages")
    }
    if "photo_file_ids" not in broadcast_msg_columns:
        connection.execute(
            text("ALTER TABLE chat_broadcast_messages ADD COLUMN photo_file_ids TEXT")
        )
    if "buttons_json" not in broadcast_msg_columns:
        connection.execute(
            text("ALTER TABLE chat_broadcast_messages ADD COLUMN buttons_json TEXT")
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
