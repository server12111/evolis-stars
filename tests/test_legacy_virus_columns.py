import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base, _add_missing_user_columns
from bot.database.models import User


class LegacyVirusColumnsTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_user_insert_works_on_db_with_legacy_not_null_virus_column(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE users ADD COLUMN virus_last_used_at DATETIME"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN virus_bonus_attempt BOOLEAN NOT NULL DEFAULT 0"))
            # Mimic the production table: NOT NULL with no SQL default.
            await conn.execute(text("ALTER TABLE users DROP COLUMN virus_bonus_attempt"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN virus_bonus_attempt BOOLEAN NOT NULL DEFAULT 0"))
            await conn.run_sync(_add_missing_user_columns)
            cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(users)"))).all()]
        self.assertNotIn("virus_bonus_attempt", cols)
        self.assertNotIn("virus_last_used_at", cols)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            session.add(User(user_id=5, first_name="N"))
            await session.commit()
        await engine.dispose()
