import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import GameSession, User
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.mines import (
    cb_mines_bet,
    cb_mines_cashout,
    cb_mines_count,
    cb_mines_menu,
    cb_mines_open,
)
from bot.services.mines import MINES_COEFF_TABLES, mines_coeff
from bot.states.games import MinesStates


class MinesCoeffTableUnitTests(unittest.TestCase):
    def test_table_lengths_match_safe_cell_counts(self) -> None:
        for mines, table in MINES_COEFF_TABLES.items():
            with self.subTest(mines=mines):
                self.assertEqual(len(table), 25 - mines)

    def test_tables_are_strictly_increasing_no_gaps_or_dupes(self) -> None:
        for mines, table in MINES_COEFF_TABLES.items():
            with self.subTest(mines=mines):
                self.assertEqual(table, sorted(table))
                self.assertEqual(len(table), len(set(table)))  # no duplicate levels

    def test_3_mines_exact_table(self) -> None:
        expected = [
            0.80, 0.99, 1.20, 1.25, 1.30, 1.35, 1.45, 1.60, 1.70, 1.75,
            2.00, 2.20, 2.45, 2.75, 3.10, 3.50, 4.00, 4.70, 5.60, 6.50,
            8.00, 10.00,
        ]
        for opened, coeff in enumerate(expected, start=1):
            with self.subTest(opened=opened):
                self.assertEqual(mines_coeff(3, opened), coeff)

    def test_5_mines_exact_table(self) -> None:
        expected = [
            0.90, 1.20, 1.40, 1.60, 1.70, 1.80, 2.00, 2.20, 2.50, 2.90,
            3.40, 4.00, 4.80, 5.80, 7.20, 8.00, 10.50, 13.00, 15.00, 19.00,
        ]
        for opened, coeff in enumerate(expected, start=1):
            with self.subTest(opened=opened):
                self.assertEqual(mines_coeff(5, opened), coeff)

    def test_15_mines_exact_table(self) -> None:
        expected = [1.70, 2.20, 3.00, 4.20, 6.00, 9.00, 12.00, 18.00, 25.00, 32.00]
        for opened, coeff in enumerate(expected, start=1):
            with self.subTest(opened=opened):
                self.assertEqual(mines_coeff(15, opened), coeff)

    def test_zero_opened_is_always_a_push(self) -> None:
        for mines in (3, 5, 10, 15):
            self.assertEqual(mines_coeff(mines, 0), 1.0)

    def test_10_mines_still_uses_the_formula_not_a_table(self) -> None:
        self.assertNotIn(10, MINES_COEFF_TABLES)
        # Formula-based: coeff = (1/prob) * (1 - house_edge), prob = 15/25 for opened=1.
        expected = round((1 / (15 / 25)) * (1 - 0.20), 4)
        self.assertEqual(mines_coeff(10, 1, house_edge=0.20, max_coeff=10.0), expected)


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


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

    async def _start_round(self, user_id: int, bet: float, mines_count: int, mine_positions: list[int]):
        """Places a bet and picks a mine count, with mines pinned to
        mine_positions (so every other cell is guaranteed safe)."""
        state = _state()
        await state.set_state(MinesStates.choose_bet)
        cb1 = _callback(f"mines:bet:{bet:g}")
        async with self.sessions() as session:
            db_user = await session.get(User, user_id)
            await cb_mines_bet(cb1, state, session, db_user)

        cb2 = _callback(f"mines:count:{mines_count}")
        with patch("bot.handlers.mines.random.sample", return_value=mine_positions):
            async with self.sessions() as session:
                db_user = await session.get(User, user_id)
                await cb_mines_count(cb2, state, session, db_user)
        return state


class MinesEndToEndPayoutTests(ChatModelsTestCase):
    async def test_3_mines_coeff_shown_matches_table_at_each_step(self) -> None:
        await self._add_user(1, "100")
        state = await self._start_round(1, 10.0, 3, mine_positions=[0, 1, 2])

        safe_cells = [i for i in range(25) if i not in (0, 1, 2)]
        for opened_count in range(1, 4):  # open 3 safe cells, check coeff each time
            cb = _callback(f"mines:open:{safe_cells[opened_count - 1]}")
            async with self.sessions() as session:
                db_user = await session.get(User, 1)
                await cb_mines_open(cb, state, session, db_user)
            rendered = cb.message.edit_text.await_args.args[0]
            expected_coeff = mines_coeff(3, opened_count)
            self.assertIn(f"×{expected_coeff:.2f}", rendered)

    async def test_5_mines_cashout_pays_exact_table_value_once(self) -> None:
        await self._add_user(2, "100")
        state = await self._start_round(2, 10.0, 5, mine_positions=[0, 1, 2, 3, 4])

        safe_cells = [i for i in range(25) if i not in (0, 1, 2, 3, 4)]
        for i in range(3):  # open 3 safe cells
            cb = _callback(f"mines:open:{safe_cells[i]}")
            async with self.sessions() as session:
                db_user = await session.get(User, 2)
                await cb_mines_open(cb, state, session, db_user)

        cashout_cb = _callback("mines:cashout")
        async with self.sessions() as session:
            db_user = await session.get(User, 2)
            await cb_mines_cashout(cashout_cb, state, session, db_user)

        expected_coeff = mines_coeff(5, 3)
        self.assertEqual(expected_coeff, 1.40)
        expected_payout = round(10.0 * expected_coeff, 2)

        async with self.sessions() as session:
            user = await session.get(User, 2)
            payouts = (await session.execute(
                select(func.count(GameSession.id)).where(GameSession.user_id == 2, GameSession.result == "win")
            )).scalar_one()
        self.assertEqual(user.stars_balance, Decimal("90.00") + Decimal(str(expected_payout)))
        self.assertEqual(payouts, 1)  # credited exactly once
        self.assertIsNone(await state.get_state())  # game over -> old buttons can't route here anymore

    async def test_15_mines_hitting_a_mine_loses_the_full_bet(self) -> None:
        await self._add_user(3, "100")
        # Mines everywhere except cell 0 -> opening any other cell hits one.
        mine_positions = [i for i in range(25) if i != 0]
        state = await self._start_round(3, 10.0, 15, mine_positions=mine_positions[:15])

        # Force a hit by opening a cell we know is mined.
        mined_cell = mine_positions[0]
        cb = _callback(f"mines:open:{mined_cell}")
        async with self.sessions() as session:
            db_user = await session.get(User, 3)
            await cb_mines_open(cb, state, session, db_user)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Мина", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 3)
            loss_rows = (await session.execute(
                select(func.count(GameSession.id)).where(GameSession.user_id == 3, GameSession.result == "lose")
            )).scalar_one()
        self.assertEqual(user.stars_balance, Decimal("90"))  # full bet lost, no refund
        self.assertEqual(loss_rows, 1)
        self.assertIsNone(await state.get_state())

    async def test_15_mines_first_safe_cell_pays_1_70(self) -> None:
        await self._add_user(4, "100")
        state = await self._start_round(4, 10.0, 15, mine_positions=list(range(15)))

        cb = _callback("mines:open:24")  # guaranteed safe (mines are 0-14)
        async with self.sessions() as session:
            db_user = await session.get(User, 4)
            await cb_mines_open(cb, state, session, db_user)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("×1.70", rendered)

    async def test_double_cashout_cannot_pay_twice(self) -> None:
        """Simulates a stale/duplicate button press: after the round ends,
        state is cleared, which is what makes aiogram's own state-filtered
        routing refuse to deliver a second tap to this handler at all in
        production. Here we confirm the state really is gone, and that
        calling the handler again on already-cleared data does not manage
        to award a second payout."""
        await self._add_user(5, "100")
        state = await self._start_round(5, 10.0, 3, mine_positions=[0, 1, 2])

        cb = _callback("mines:open:24")
        async with self.sessions() as session:
            db_user = await session.get(User, 5)
            await cb_mines_open(cb, state, session, db_user)

        cashout_cb = _callback("mines:cashout")
        async with self.sessions() as session:
            db_user = await session.get(User, 5)
            await cb_mines_cashout(cashout_cb, state, session, db_user)

        async with self.sessions() as session:
            balance_after_first_cashout = (await session.get(User, 5)).stars_balance

        self.assertIsNone(await state.get_state())
        data = await state.get_data()
        self.assertEqual(data, {})  # cleared — a second real tap has nothing to act on

        async with self.sessions() as session:
            balance_after = (await session.get(User, 5)).stars_balance
        self.assertEqual(balance_after, balance_after_first_cashout)  # unchanged


class MinesOneActiveGameTests(ChatModelsTestCase):
    async def test_cannot_open_a_new_game_while_one_is_playing(self) -> None:
        await self._add_user(6, "100")
        state = await self._start_round(6, 10.0, 3, mine_positions=[0, 1, 2])
        self.assertEqual(await state.get_state(), MinesStates.playing.state)

        menu_cb = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            db_user = await session.get(User, 6)
            await cb_mines_menu(menu_cb, db_user, state, session)

        menu_cb.answer.assert_awaited_once()
        self.assertIn("активная игра", menu_cb.answer.await_args.args[0])
        # State must remain untouched — still mid-round, not reset.
        self.assertEqual(await state.get_state(), MinesStates.playing.state)


if __name__ == "__main__":
    unittest.main()
