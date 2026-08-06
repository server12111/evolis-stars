import unittest

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base, _add_missing_user_columns
from bot.database.models import ChatBroadcastMessage


class LegacyBroadcastTextIsHtmlMigrationTests(unittest.IsolatedAsyncioTestCase):
    """A production DB predates text_is_html entirely -- every existing
    chat_broadcast_messages row was captured as raw, possibly-unescaped
    plain text before premium-emoji/HTML support existed. The migration
    must backfill them to False so the scheduler keeps sending them with
    parse_mode=None, not suddenly try to HTML-parse old raw text and crash
    on a literal "<" or "&"."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text("DROP TABLE chat_broadcast_messages"))
            await connection.execute(text(
                "CREATE TABLE chat_broadcast_messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "chat_id BIGINT NOT NULL, "
                "text TEXT NOT NULL, "
                "photo_file_ids TEXT, buttons_json TEXT, "
                "status VARCHAR(16) NOT NULL DEFAULT 'approved', "
                "moderation_channel_message_id INTEGER, "
                "created_at DATETIME)"
            ))
            await connection.execute(text(
                "INSERT INTO chat_broadcast_messages (chat_id, text) VALUES (-1, 'legacy raw text')"
            ))
            await connection.run_sync(_add_missing_user_columns)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_existing_row_backfilled_to_false(self) -> None:
        async with self.sessions() as session:
            rows = (await session.execute(select(ChatBroadcastMessage))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].text_is_html)

    async def test_migration_is_idempotent_on_repeated_startup(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(_add_missing_user_columns)
        async with self.sessions() as session:
            rows = (await session.execute(select(ChatBroadcastMessage))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].text_is_html)


if __name__ == "__main__":
    unittest.main()
