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


def _choice_side_effect(seq):
    """random.choice is called twice inside cb_random — once for the stake
    (a sequence of floats) and once for the game (a sequence of strings).
    Force deterministic picks for both without caring about call order."""
    seq = list(seq)
    if seq and isinstance(seq[0], float):
        return 3.0
    return "wheel"


class RandomButtonTests(ChatModelsTestCase):
    async def test_grants_a_free_game_credit_not_stars(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U", stars_balance=Decimal("10")))
            await session.commit()

        cb = _callback()
        with patch("bot.handlers.random_game.random.choice", side_effect=_choice_side_effect):
            async with self.sessions() as session:
                db_user = await session.get(User, 1)
                await cb_random(cb, db_user, session)

        cb.message.answer.assert_awaited_once()
        args, kwargs = cb.message.answer.await_args
        rendered = args[0]
        self.assertIn("Рандом", rendered)
        self.assertIn("3.00", rendered)  # forced stake choice above

        markup = kwargs["reply_markup"]
        play_button = markup.inline_keyboard[0][0]
        self.assertIn("Играть", play_button.text)
        self.assertEqual(play_button.callback_data, "menu:wheel")

        async with self.sessions() as session:
            user = await session.get(User, 1)
        # Balance is untouched — the reward is a separate "free game" credit.
        self.assertEqual(user.stars_balance, Decimal("10"))
        self.assertEqual(user.free_game_credit_amount, Decimal("3.0"))
        self.assertIsNotNone(user.last_random_at)

    async def test_stake_is_randomized_across_1_2_3(self) -> None:
        seen: set[Decimal] = set()
        for i, forced in enumerate((1.0, 2.0, 3.0)):
            user_id = 100 + i
            async with self.sessions() as session:
                session.add(User(user_id=user_id, first_name="U", stars_balance=Decimal("0")))
                await session.commit()

            cb = _callback()
            with patch("bot.handlers.random_game.random.choice", side_effect=lambda seq, f=forced: (
                f if (seq and isinstance(list(seq)[0], float)) else "wheel"
            )):
                async with self.sessions() as session:
                    db_user = await session.get(User, user_id)
                    await cb_random(cb, db_user, session)

            async with self.sessions() as session:
                user = await session.get(User, user_id)
            seen.add(user.free_game_credit_amount)

        self.assertEqual(seen, {Decimal("1.0"), Decimal("2.0"), Decimal("3.0")})

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
        with patch("bot.handlers.random_game.random.choice", side_effect=_choice_side_effect):
            async with self.sessions() as session:
                db_user = await session.get(User, 4)
                await cb_random(cb, db_user, session)

        cb.message.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
