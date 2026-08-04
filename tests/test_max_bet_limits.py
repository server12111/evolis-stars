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
from bot.handlers.games import msg_bet_enter
from bot.handlers.group.games_doors import msg_doors_start
from bot.handlers.group.games_maze import msg_maze_start
from bot.handlers.group.games_roulette import msg_roulette_bet
from bot.handlers.group.games_tower import msg_tower_start as msg_chat_tower_start
from bot.handlers.mines import cb_mines_bet, msg_mines_bet_custom
from bot.handlers.tower import cb_tower_bet
from bot.handlers.wheel import msg_wheel_bet
from bot.services.chat_games import place_bet
from bot.states.games import GameStates, MinesStates, TowerStates, WheelStates


def _state(state_cls=None) -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _message(text: str):
    return SimpleNamespace(text=text, answer=AsyncMock())


def _callback(data: str):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, data=data, answer=AsyncMock())


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _add_user(self, user_id: int, balance: str) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=user_id, first_name="U", stars_balance=Decimal(balance)))
            await session.commit()


class PlaceBetMaxLimitTests(ChatModelsTestCase):
    async def test_bet_over_max_is_rejected(self) -> None:
        await self._add_user(1, "10000")
        async with self.sessions() as session:
            ok, error = await place_bet(session, 1, 600.0, 1.0, 500.0)
        self.assertFalse(ok)
        self.assertIn("Макс. ставка", error)

    async def test_bet_at_max_is_accepted(self) -> None:
        await self._add_user(2, "10000")
        async with self.sessions() as session:
            ok, error = await place_bet(session, 2, 500.0, 1.0, 500.0)
        self.assertTrue(ok)

    async def test_no_max_bet_means_unlimited(self) -> None:
        await self._add_user(3, "10000")
        async with self.sessions() as session:
            ok, error = await place_bet(session, 3, 9999.0, 1.0)
        self.assertTrue(ok)


class DiceGamesMaxBetTests(ChatModelsTestCase):
    async def test_over_max_bet_is_rejected_with_no_deduction(self) -> None:
        await self._add_user(10, "10000")
        state = _state()
        await state.set_state(GameStates.enter_bet)
        await state.update_data(game_type="dice", bet_step=1.0)
        message = _message("501")

        async with self.sessions() as session:
            db_user = await session.get(User, 10)
            await msg_bet_enter(message, session, db_user, state)

        rendered = message.answer.await_args.args[0]
        self.assertIn("Макс. ставка", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 10)
        self.assertEqual(user.stars_balance, Decimal("10000"))  # untouched


class WheelMaxBetTests(ChatModelsTestCase):
    async def test_over_max_bet_is_rejected(self) -> None:
        await self._add_user(20, "10000")
        state = _state()
        await state.set_state(WheelStates.entering_bet)
        message = _message("501")

        async with self.sessions() as session:
            db_user = await session.get(User, 20)
            await msg_wheel_bet(message, state, session, AsyncMock(), db_user)

        rendered = message.answer.await_args.args[0]
        self.assertIn("Макс. ставка", rendered)


class MinesMaxBetTests(ChatModelsTestCase):
    async def test_custom_bet_over_max_is_rejected(self) -> None:
        await self._add_user(30, "10000")
        state = _state()
        message = _message("501")

        async with self.sessions() as session:
            db_user = await session.get(User, 30)
            await msg_mines_bet_custom(message, state, session, db_user)

        rendered = message.answer.await_args.args[0]
        self.assertIn("Макс. ставка", rendered)

    async def test_preset_bet_over_max_is_rejected(self) -> None:
        await self._add_user(31, "10000")
        state = _state()
        await state.set_state(MinesStates.choose_bet)
        cb = _callback("mines:bet:501")

        async with self.sessions() as session:
            db_user = await session.get(User, 31)
            await cb_mines_bet(cb, state, session, db_user)

        cb.answer.assert_awaited_once()
        self.assertIn("Макс. ставка", cb.answer.await_args.args[0])


class TowerMaxBetTests(ChatModelsTestCase):
    async def test_preset_bet_over_max_is_rejected(self) -> None:
        await self._add_user(40, "10000")
        state = _state()
        await state.set_state(TowerStates.choose_bet)
        cb = _callback("tower:bet:501")

        async with self.sessions() as session:
            db_user = await session.get(User, 40)
            await cb_tower_bet(cb, state, session, db_user)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Макс. ставка", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 40)
        self.assertEqual(user.stars_balance, Decimal("10000"))  # untouched


def _group_message(chat_id: int, user_id: int, text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Chat"),
        from_user=SimpleNamespace(id=user_id, first_name="U"),
        text=text,
        reply=AsyncMock(),
        answer=AsyncMock(),
    )


class GroupGamesMaxBetTests(ChatModelsTestCase):
    async def test_roulette_over_max_bet_is_rejected(self) -> None:
        await self._add_user(50, "10000")
        message = _group_message(-1, 50, "ред 600")
        async with self.sessions() as session:
            await msg_roulette_bet(message, session)
        rendered = message.reply.await_args.args[0]
        self.assertIn("Макс. ставка", rendered)

    async def test_maze_over_max_bet_is_rejected(self) -> None:
        await self._add_user(51, "10000")
        message = _group_message(-2, 51, "лабиринт 600")
        async with self.sessions() as session:
            await msg_maze_start(message, session)
        rendered = message.reply.await_args.args[0]
        self.assertIn("Макс. ставка", rendered)

    async def test_doors_over_max_bet_is_rejected(self) -> None:
        await self._add_user(52, "10000")
        message = _group_message(-3, 52, "двери 600")
        async with self.sessions() as session:
            await msg_doors_start(message, session)
        rendered = message.reply.await_args.args[0]
        self.assertIn("Макс. ставка", rendered)

    async def test_chat_tower_over_max_bet_is_rejected(self) -> None:
        await self._add_user(53, "10000")
        message = _group_message(-4, 53, "башня 600")
        async with self.sessions() as session:
            await msg_chat_tower_start(message, session)
        rendered = message.reply.await_args.args[0]
        self.assertIn("Макс. ставка", rendered)


if __name__ == "__main__":
    unittest.main()
