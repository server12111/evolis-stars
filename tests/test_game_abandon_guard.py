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
from bot.handlers.casino import cb_casino_menu
from bot.handlers.games import cb_games_menu
from bot.handlers.start import cb_main_menu
from bot.services.game_abandon_guard import guard_active_game
from bot.states.games import GameStates, MinesStates, TowerStates


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _callback() -> SimpleNamespace:
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(message=message, answer=AsyncMock())


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _add_user(self, user_id: int, balance: str) -> User:
        async with self.sessions() as session:
            user = User(user_id=user_id, first_name="U", stars_balance=Decimal(balance))
            session.add(user)
            await session.commit()
        return user


class GuardActiveGameTests(ChatModelsTestCase):
    async def test_mines_in_progress_blocks_and_keeps_balance(self) -> None:
        await self._add_user(1, "90")
        state = _state()
        await state.set_state(MinesStates.playing)
        await state.update_data(bet=10, gems=2)

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 1)
            blocked = await guard_active_game(cb, session, db_user, state)

        self.assertTrue(blocked)
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        async with self.sessions() as session:
            user = await session.get(User, 1)
        self.assertEqual(user.stars_balance, Decimal("90"))  # unchanged, still at risk
        self.assertEqual(await state.get_state(), MinesStates.playing.state)  # untouched

    async def test_mines_no_progress_refunds_and_clears(self) -> None:
        await self._add_user(2, "90")
        state = _state()
        await state.set_state(MinesStates.playing)
        await state.update_data(bet=10, gems=0)

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 2)
            blocked = await guard_active_game(cb, session, db_user, state)

        self.assertFalse(blocked)
        async with self.sessions() as session:
            user = await session.get(User, 2)
        self.assertEqual(user.stars_balance, Decimal("100"))  # 10 refunded
        self.assertIsNone(await state.get_state())

    async def test_mines_no_progress_free_credit_not_double_refunded(self) -> None:
        await self._add_user(3, "90")
        state = _state()
        await state.set_state(MinesStates.playing)
        await state.update_data(bet=10, gems=0, used_free_credit=True)

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 3)
            await guard_active_game(cb, session, db_user, state)

        async with self.sessions() as session:
            user = await session.get(User, 3)
        self.assertEqual(user.stars_balance, Decimal("90"))  # no phantom refund

    async def test_tower_in_progress_blocks(self) -> None:
        await self._add_user(4, "90")
        state = _state()
        await state.set_state(TowerStates.playing)
        await state.update_data(bet=10, level=2)

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 4)
            blocked = await guard_active_game(cb, session, db_user, state)

        self.assertTrue(blocked)
        async with self.sessions() as session:
            user = await session.get(User, 4)
        self.assertEqual(user.stars_balance, Decimal("90"))

    async def test_tower_no_progress_refunds(self) -> None:
        await self._add_user(5, "90")
        state = _state()
        await state.set_state(TowerStates.playing)
        await state.update_data(bet=10, level=0)

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 5)
            blocked = await guard_active_game(cb, session, db_user, state)

        self.assertFalse(blocked)
        async with self.sessions() as session:
            user = await session.get(User, 5)
        self.assertEqual(user.stars_balance, Decimal("100"))

    async def test_dice_side_selection_refunds(self) -> None:
        await self._add_user(6, "90")
        state = _state()
        await state.set_state(GameStates.choose_dice_side)
        await state.update_data(bet=10)

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 6)
            blocked = await guard_active_game(cb, session, db_user, state)

        self.assertFalse(blocked)
        async with self.sessions() as session:
            user = await session.get(User, 6)
        self.assertEqual(user.stars_balance, Decimal("100"))

    async def test_no_active_game_is_a_no_op(self) -> None:
        await self._add_user(7, "90")
        state = _state()

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 7)
            blocked = await guard_active_game(cb, session, db_user, state)

        self.assertFalse(blocked)
        async with self.sessions() as session:
            user = await session.get(User, 7)
        self.assertEqual(user.stars_balance, Decimal("90"))


class HubHandlersUseTheGuardTests(ChatModelsTestCase):
    """Reaching each hub screen via a stale button while a real paid game
    is mid-flight must never silently forfeit the stake."""

    async def _assert_blocks_mines_in_progress(self, handler, extra_kwargs) -> None:
        await self._add_user(10, "90")
        state = _state()
        await state.set_state(MinesStates.playing)
        await state.update_data(bet=10, gems=1)

        cb = _callback()
        async with self.sessions() as session:
            db_user = await session.get(User, 10)
            await handler(callback=cb, db_user=db_user, state=state, session=session, **extra_kwargs)

        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        async with self.sessions() as session:
            user = await session.get(User, 10)
        self.assertEqual(user.stars_balance, Decimal("90"))

    async def test_menu_main_blocks_mid_game_mines(self) -> None:
        await self._assert_blocks_mines_in_progress(cb_main_menu, {})

    async def test_menu_casino_blocks_mid_game_mines(self) -> None:
        await self._assert_blocks_mines_in_progress(cb_casino_menu, {})

    async def test_menu_games_blocks_mid_game_mines(self) -> None:
        await self._assert_blocks_mines_in_progress(cb_games_menu, {})


if __name__ == "__main__":
    unittest.main()
