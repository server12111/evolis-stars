import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.database.repositories.user import UserRepository
from bot.handlers.withdraw import _WITHDRAW_MIN_RECENT_REFERRALS, cb_withdraw_menu


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
    )


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class ReferralsCountSinceTests(DbTestCase):
    async def test_counts_only_referrals_created_within_the_window(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="Referrer"))
            session.add(User(user_id=2, first_name="Recent", referrer_id=1, created_at=now - timedelta(hours=1)))
            session.add(User(user_id=3, first_name="Recent2", referrer_id=1, created_at=now - timedelta(hours=23)))
            session.add(User(user_id=4, first_name="Stale", referrer_id=1, created_at=now - timedelta(days=2)))
            session.add(User(user_id=5, first_name="Unrelated", referrer_id=99, created_at=now))
            await session.commit()

            count = await UserRepository(session).referrals_count_since(1, now - timedelta(hours=24))

        self.assertEqual(count, 2)


class WithdrawMenuGateTests(DbTestCase):
    async def test_blocks_with_fewer_than_the_minimum_recent_referrals(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            referrer = User(user_id=1, first_name="Referrer")
            session.add(referrer)
            # Only 2 recent referrals -- one short of the minimum.
            session.add(User(user_id=2, first_name="A", referrer_id=1, created_at=now - timedelta(hours=1)))
            session.add(User(user_id=3, first_name="B", referrer_id=1, created_at=now - timedelta(hours=2)))
            await session.commit()

            cb = callback()
            state = SimpleNamespace(clear=AsyncMock())
            await cb_withdraw_menu(cb, referrer, session, state)

        state.clear.assert_not_awaited()
        cb.answer.assert_awaited_once()
        args, kwargs = cb.answer.await_args
        self.assertIn(str(_WITHDRAW_MIN_RECENT_REFERRALS), args[0])
        self.assertTrue(kwargs.get("show_alert"))
        cb.message.edit_text.assert_not_awaited()

    async def test_proceeds_with_at_least_the_minimum_recent_referrals(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            referrer = User(user_id=1, first_name="Referrer")
            session.add(referrer)
            for uid in (2, 3, 4):
                session.add(User(user_id=uid, first_name=f"R{uid}", referrer_id=1, created_at=now - timedelta(hours=1)))
            await session.commit()

            cb = callback()
            state = SimpleNamespace(clear=AsyncMock())
            await cb_withdraw_menu(cb, referrer, session, state)

        state.clear.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        cb.answer.assert_awaited_once_with()

    async def test_referrals_older_than_24h_do_not_count(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            referrer = User(user_id=1, first_name="Referrer")
            session.add(referrer)
            for uid in (2, 3, 4):
                session.add(User(user_id=uid, first_name=f"R{uid}", referrer_id=1, created_at=now - timedelta(days=2)))
            await session.commit()

            cb = callback()
            state = SimpleNamespace(clear=AsyncMock())
            await cb_withdraw_menu(cb, referrer, session, state)

        state.clear.assert_not_awaited()
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))


if __name__ == "__main__":
    unittest.main()
