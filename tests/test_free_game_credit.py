import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.cases import cb_cases_confirm
from bot.handlers.mines import cb_mines_bet, cb_mines_count
from bot.handlers.random_game import cb_random
from bot.handlers.tower import cb_tower_bet
from bot.handlers.wheel import cb_wheel_bet
from bot.states.games import MinesStates, TowerStates


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _callback(data: str):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(message=message, data=data, answer=AsyncMock())


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class RandomGrantsCreditTests(ChatModelsTestCase):
    async def test_cb_random_never_touches_balance(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
        cb = SimpleNamespace(message=SimpleNamespace(answer=AsyncMock()), answer=AsyncMock())
        async with self.sessions() as session:
            db_user = await session.get(User, 1)
            await cb_random(cb, db_user, session)
        async with self.sessions() as session:
            user = await session.get(User, 1)
        self.assertEqual(user.stars_balance, Decimal("0"))
        self.assertIn(user.free_game_credit_amount, (Decimal("1.0"), Decimal("2.0"), Decimal("3.0")))


class WheelFreeCreditTests(ChatModelsTestCase):
    async def test_3_star_bet_with_credit_does_not_touch_balance_on_deduction(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=10, first_name="U", stars_balance=Decimal("0"), free_game_credit_amount=Decimal("3.0")))
            await session.commit()
            db_user = await session.get(User, 10)
            cb = _callback("wheel:bet:3")
            await cb_wheel_bet(cb, session, AsyncMock(), db_user)

        async with self.sessions() as session:
            user = await session.get(User, 10)
        self.assertIsNone(user.free_game_credit_amount)
        # Balance started at 0 and was never charged the 3⭐ bet — any
        # nonzero balance now is purely the payout (0.1x or 50x of 3⭐),
        # never negative, and never short by exactly the stake.
        self.assertGreaterEqual(user.stars_balance, Decimal("0"))

    async def test_3_star_bet_without_credit_requires_balance(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=11, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
            db_user = await session.get(User, 11)
            cb = _callback("wheel:bet:3")
            await cb_wheel_bet(cb, session, AsyncMock(), db_user)

        cb.answer.assert_awaited_once()
        self.assertIn("Недостаточно", cb.answer.await_args.args[0])


class CasesFreeCreditTests(ChatModelsTestCase):
    async def test_tier_3_case_with_credit_does_not_charge_balance(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=20, first_name="U", stars_balance=Decimal("0"), free_game_credit_amount=Decimal("3.0")))
            await session.commit()
            db_user = await session.get(User, 20)
            cb = _callback("cases:confirm:3")
            await cb_cases_confirm(cb, session, AsyncMock(), db_user)

        async with self.sessions() as session:
            user = await session.get(User, 20)
        self.assertIsNone(user.free_game_credit_amount)
        self.assertGreaterEqual(user.stars_balance, Decimal("0"))


class MinesFreeCreditTests(ChatModelsTestCase):
    async def test_3_star_bet_with_credit_reaches_playing_state_with_zero_balance(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=30, first_name="U", stars_balance=Decimal("0"), free_game_credit_amount=Decimal("3.0")))
            await session.commit()

        state = _state()
        await state.set_state(MinesStates.choose_bet)
        cb1 = _callback("mines:bet:3")
        async with self.sessions() as session:
            db_user = await session.get(User, 30)
            await cb_mines_bet(cb1, state, session, db_user)

        cb2 = _callback("mines:count:3")
        async with self.sessions() as session:
            db_user = await session.get(User, 30)
            await cb_mines_count(cb2, state, session, db_user)

        async with self.sessions() as session:
            user = await session.get(User, 30)
        self.assertIsNone(user.free_game_credit_amount)
        self.assertEqual(user.stars_balance, Decimal("0"))  # never charged
        data = await state.get_data()
        self.assertEqual(data["bet"], 3.0)


class TowerFreeCreditTests(ChatModelsTestCase):
    async def test_3_star_bet_with_credit_does_not_charge_balance(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=40, first_name="U", stars_balance=Decimal("0"), free_game_credit_amount=Decimal("3.0")))
            await session.commit()

        state = _state()
        await state.set_state(TowerStates.choose_bet)
        cb = _callback("tower:bet:3")
        async with self.sessions() as session:
            db_user = await session.get(User, 40)
            await cb_tower_bet(cb, state, session, db_user)

        async with self.sessions() as session:
            user = await session.get(User, 40)
        self.assertIsNone(user.free_game_credit_amount)
        self.assertEqual(user.stars_balance, Decimal("0"))  # never charged
        data = await state.get_data()
        self.assertEqual(data["bet"], 3.0)


if __name__ == "__main__":
    unittest.main()
