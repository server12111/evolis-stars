import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.random_game import cb_random


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


def _callback():
    message = SimpleNamespace(answer=AsyncMock())
    return SimpleNamespace(message=message, answer=AsyncMock())


class RandomButtonTests(ChatModelsTestCase):
    async def test_wheel_win_credits_balance_and_sets_cooldown(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U", stars_balance=Decimal("10")))
            await session.commit()

        cb = _callback()
        with patch("bot.handlers.random_game.random.choice", return_value="wheel"), \
                patch("bot.handlers.random_game.get_wheel_outcome", AsyncMock(return_value=50.0)):
            async with self.sessions() as session:
                db_user = await session.get(User, 1)
                await cb_random(cb, db_user, session)

        cb.message.answer.assert_awaited_once()
        rendered = cb.message.answer.await_args.args[0]
        self.assertIn("Колесо", rendered)
        self.assertIn("150.00", rendered)  # 3 stake * 50.0 coeff

        async with self.sessions() as session:
            user = await session.get(User, 1)
        self.assertEqual(user.stars_balance, Decimal("10") + Decimal("150.00"))
        self.assertIsNotNone(user.last_random_at)

    async def test_case_outcome_credits_flat_prize(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=2, first_name="U", stars_balance=Decimal("10")))
            await session.commit()

        cb = _callback()
        with patch("bot.handlers.random_game.random.choice", return_value="case_3"), \
                patch("bot.handlers.random_game.get_case_outcome", AsyncMock(return_value=5.0)):
            async with self.sessions() as session:
                db_user = await session.get(User, 2)
                await cb_random(cb, db_user, session)

        rendered = cb.message.answer.await_args.args[0]
        self.assertIn("Кейс", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 2)
        self.assertEqual(user.stars_balance, Decimal("10") + Decimal("5.0"))

    async def test_second_tap_within_cooldown_is_blocked(self) -> None:
        async with self.sessions() as session:
            session.add(User(
                user_id=3, first_name="U", stars_balance=Decimal("10"),
                last_random_at=datetime.utcnow() - timedelta(hours=1),
            ))
            await session.commit()

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 3)
            await cb_random(cb, db_user, session)

        cb.message.answer.assert_not_awaited()
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        async with self.sessions() as session:
            user = await session.get(User, 3)
        self.assertEqual(user.stars_balance, Decimal("10"))  # unchanged

    async def test_cooldown_expired_allows_replay(self) -> None:
        async with self.sessions() as session:
            session.add(User(
                user_id=4, first_name="U", stars_balance=Decimal("10"),
                last_random_at=datetime.utcnow() - timedelta(hours=25),
            ))
            await session.commit()

        cb = _callback()
        with patch("bot.handlers.random_game.random.choice", return_value="wheel"), \
                patch("bot.handlers.random_game.get_wheel_outcome", AsyncMock(return_value=0.1)):
            async with self.sessions() as session:
                db_user = await session.get(User, 4)
                await cb_random(cb, db_user, session)

        cb.message.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
