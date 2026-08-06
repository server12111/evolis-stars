import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.tower import cb_tower_bet, cb_tower_cashout, cb_tower_pick


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


class PrivateTowerTests(ChatModelsTestCase):
    async def test_normal_mode_has_one_mine_per_level(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U", stars_balance=Decimal("100")))
            await session.commit()

        state = _state()
        cb = _callback("tower:bet:10")
        async with self.sessions() as session:
            db_user = await session.get(User, 1)
            await cb_tower_bet(cb, state, session, db_user)

        data = await state.get_data()
        self.assertEqual(len(data["mines"][0]), 1)

    async def test_recovery_mode_doubles_mine_count(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=2, first_name="U", stars_balance=Decimal("100")))
            repo = SettingsRepository(session)
            await repo.add_float("tower_total_bet", 100.0)
            await repo.add_float("tower_total_payout", 150.0)  # house net-negative
            await session.commit()

        state = _state()
        cb = _callback("tower:bet:10")
        async with self.sessions() as session:
            db_user = await session.get(User, 2)
            await cb_tower_bet(cb, state, session, db_user)

        data = await state.get_data()
        self.assertEqual(len(data["mines"][0]), 2)

    async def test_hitting_any_mine_slot_ends_the_round_and_tracks_ledger(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=3, first_name="U", stars_balance=Decimal("100")))
            await session.commit()

        state = _state()
        with patch("bot.handlers.tower.random.sample", return_value=[0, 1]):
            cb = _callback("tower:bet:10")
            async with self.sessions() as session:
                db_user = await session.get(User, 3)
                await cb_tower_bet(cb, state, session, db_user)

        pick_cb = _callback("tower:pick:1")
        async with self.sessions() as session:
            db_user = await session.get(User, 3)
            await cb_tower_pick(pick_cb, state, session, db_user)

        rendered = pick_cb.message.edit_text.await_args.args[0]
        self.assertIn("Мина", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 3)
            repo = SettingsRepository(session)
            total_bet = await repo.get_float("tower_total_bet")
            total_payout = await repo.get_float("tower_total_payout")
        self.assertEqual(user.stars_balance, Decimal("90"))
        self.assertEqual(total_bet, 10.0)
        self.assertEqual(total_payout, 0.0)

    async def test_cashout_after_one_level_pays_coeff_0_and_tracks_ledger(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=4, first_name="U", stars_balance=Decimal("100")))
            await session.commit()

        state = _state()
        with patch("bot.handlers.tower.random.sample", return_value=[2]):
            cb = _callback("tower:bet:10")
            async with self.sessions() as session:
                db_user = await session.get(User, 4)
                await cb_tower_bet(cb, state, session, db_user)

            pick_cb = _callback("tower:pick:0")
            async with self.sessions() as session:
                db_user = await session.get(User, 4)
                await cb_tower_pick(pick_cb, state, session, db_user)

        cashout_cb = _callback("tower:cashout")
        async with self.sessions() as session:
            db_user = await session.get(User, 4)
            await cb_tower_cashout(cashout_cb, state, session, db_user)

        async with self.sessions() as session:
            user = await session.get(User, 4)
            repo = SettingsRepository(session)
            total_bet = await repo.get_float("tower_total_bet")
            total_payout = await repo.get_float("tower_total_payout")
        self.assertEqual(user.stars_balance, Decimal("90.00") + Decimal("10.50"))  # 10 * tower_coeff_0 (1.05)
        self.assertEqual(total_bet, 10.0)
        self.assertEqual(total_payout, 10.5)

    async def test_displayed_coeff_after_pick_matches_what_cashout_actually_pays(self) -> None:
        """Regression test: the coefficient/payout shown after a successful
        pick — including the number printed right on the "Забрать" button —
        must be exactly what tower:cashout pays if pressed immediately
        after. They were off by one tier before this fix."""
        async with self.sessions() as session:
            session.add(User(user_id=5, first_name="U", stars_balance=Decimal("100")))
            await session.commit()

        state = _state()
        with patch("bot.handlers.tower.random.sample", return_value=[2]):
            cb = _callback("tower:bet:10")
            async with self.sessions() as session:
                db_user = await session.get(User, 5)
                await cb_tower_bet(cb, state, session, db_user)

            pick_cb = _callback("tower:pick:0")
            async with self.sessions() as session:
                db_user = await session.get(User, 5)
                await cb_tower_pick(pick_cb, state, session, db_user)

        rendered = pick_cb.message.edit_text.await_args.args[0]
        self.assertIn("×1.05", rendered)
        self.assertIn("10.50 RP⭐️", rendered)

        kb = pick_cb.message.edit_text.await_args.kwargs["reply_markup"]
        cashout_button = next(
            b for row in kb.inline_keyboard for b in row if b.callback_data == "tower:cashout"
        )
        self.assertIn("10.50 RP⭐️", cashout_button.text)
        self.assertIn("×1.05", cashout_button.text)

        cashout_cb = _callback("tower:cashout")
        async with self.sessions() as session:
            db_user = await session.get(User, 5)
            await cb_tower_cashout(cashout_cb, state, session, db_user)

        async with self.sessions() as session:
            user = await session.get(User, 5)
        self.assertEqual(user.stars_balance, Decimal("90.00") + Decimal("10.50"))


if __name__ == "__main__":
    unittest.main()
