import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.services.referral_reminder_scheduler import _send_reminders


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # _send_reminders opens its OWN session via SessionFactory, not the
        # test's self.sessions -- point it at the same in-memory engine.
        patcher = patch(
            "bot.services.referral_reminder_scheduler.SessionFactory",
            async_sessionmaker(self.engine, expire_on_commit=False),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class ReferralReminderSchedulerTests(DbTestCase):
    async def test_reminds_a_referrer_stale_for_over_24h(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(User(user_id=1, username="ref1", first_name="R"))
            session.add(User(user_id=2, first_name="A", referrer_id=1, created_at=now - timedelta(hours=25)))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock())
        await _send_reminders(bot)

        bot.send_message.assert_awaited_once()
        args, kwargs = bot.send_message.await_args
        self.assertEqual(args[0], 1)
        self.assertIn("ref_1", args[1])

        async with self.sessions() as session:
            user = await session.get(User, 1)
            self.assertIsNotNone(user.last_referral_reminder_sent_at)

    async def test_does_not_remind_a_referrer_active_within_24h(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="R"))
            session.add(User(user_id=2, first_name="A", referrer_id=1, created_at=now - timedelta(hours=1)))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock())
        await _send_reminders(bot)

        bot.send_message.assert_not_awaited()

    async def test_does_not_remind_twice_for_the_same_inactivity_streak(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            referral_time = now - timedelta(hours=25)
            session.add(User(user_id=1, first_name="R", last_referral_reminder_sent_at=referral_time))
            session.add(User(user_id=2, first_name="A", referrer_id=1, created_at=referral_time))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock())
        await _send_reminders(bot)

        bot.send_message.assert_not_awaited()

    async def test_reminds_again_once_a_newer_referral_also_goes_stale(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            old_referral = now - timedelta(days=3)
            new_referral = now - timedelta(hours=25)
            session.add(User(user_id=1, first_name="R", last_referral_reminder_sent_at=old_referral))
            session.add(User(user_id=2, first_name="A", referrer_id=1, created_at=old_referral))
            session.add(User(user_id=3, first_name="B", referrer_id=1, created_at=new_referral))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock())
        await _send_reminders(bot)

        bot.send_message.assert_awaited_once()

    async def test_blocked_user_is_skipped(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="R", is_blocked=True))
            session.add(User(user_id=2, first_name="A", referrer_id=1, created_at=now - timedelta(hours=25)))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock())
        await _send_reminders(bot)

        bot.send_message.assert_not_awaited()

    async def test_failed_send_still_marks_the_streak_reminded(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="R"))
            session.add(User(user_id=2, first_name="A", referrer_id=1, created_at=now - timedelta(hours=25)))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=Exception("blocked bot")))
        await _send_reminders(bot)

        async with self.sessions() as session:
            user = await session.get(User, 1)
            self.assertIsNotNone(user.last_referral_reminder_sent_at)


if __name__ == "__main__":
    unittest.main()
