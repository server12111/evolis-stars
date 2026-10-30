import unittest
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base, _add_missing_user_columns
from bot.database.models import User


class ReminderBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_referrers_not_mass_reminded_after_upgrade(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        old = datetime.utcnow() - timedelta(days=3)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            session.add(User(user_id=1, first_name="R"))
            await session.flush()
            session.add(User(user_id=2, first_name="A", referrer_id=1, created_at=old))
            await session.commit()
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE users DROP COLUMN last_referral_reminder_sent_at"))
            await conn.run_sync(_add_missing_user_columns)
            rows = dict((await conn.execute(text("SELECT user_id, last_referral_reminder_sent_at FROM users"))).all())
        await engine.dispose()
        self.assertIsNotNone(rows[1])
        self.assertIsNone(rows[2])
