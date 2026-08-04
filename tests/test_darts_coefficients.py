import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.database.engine import Base
from bot.database.models import BotSettings, User
from bot.handlers.games import _execute_game, msg_bet_enter
from bot.keyboards.games import darts_side_kb
from bot.states.games import GameStates


def _fake_bot(dice_value: int) -> SimpleNamespace:
    dice_msg = SimpleNamespace(dice=SimpleNamespace(value=dice_value))
    return SimpleNamespace(send_dice=AsyncMock(return_value=dice_msg))


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class DartsPayoutMatchesTelegramResultTests(ChatModelsTestCase):
    async def test_center_hit_value_6_pays_configured_3x(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
            db_user = await session.get(User, 1)
            won, payout, value = await _execute_game(
                _fake_bot(6), 1, session, db_user, "darts", 10.0, "center",
            )
        self.assertEqual(value, 6)
        self.assertTrue(won)
        self.assertEqual(payout, 30.0)  # 10 * 3.0
        self.assertEqual(db_user.darts_bullseye_count, 1)

    async def test_bounce_value_1_pays_configured_1_7x(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=2, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
            db_user = await session.get(User, 2)
            won, payout, value = await _execute_game(
                _fake_bot(1), 1, session, db_user, "darts", 10.0, "bounce",
            )
        self.assertEqual(value, 1)
        self.assertTrue(won)
        self.assertEqual(payout, 17.0)  # 10 * 1.7

    async def test_center_side_loses_on_any_other_value(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=3, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
            db_user = await session.get(User, 3)
            won, payout, value = await _execute_game(
                _fake_bot(3), 1, session, db_user, "darts", 10.0, "center",
            )
        self.assertFalse(won)
        self.assertEqual(payout, 0.0)

    async def test_wrong_side_choice_does_not_win_even_on_a_winning_value(self) -> None:
        """A center=6 roll must not pay out the bounce side, and vice versa —
        the payout is strictly tied to (actual Telegram value, chosen side)."""
        async with self.sessions() as session:
            session.add(User(user_id=4, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
            db_user = await session.get(User, 4)
            won, payout, value = await _execute_game(
                _fake_bot(6), 1, session, db_user, "darts", 10.0, "bounce",
            )
        self.assertEqual(value, 6)
        self.assertFalse(won)
        self.assertEqual(payout, 0.0)


class DartsKeyboardShowsRealCoefficientsTests(unittest.TestCase):
    def test_button_labels_reflect_the_actual_configured_coefficients(self) -> None:
        markup = darts_side_kb(3.0, 1.7)
        texts = [btn.text for row in markup.inline_keyboard for btn in row]
        self.assertTrue(any("x3" in t for t in texts))
        self.assertTrue(any("x1.7" in t for t in texts))
        # The old hardcoded "x5" must be gone now that it's driven by settings.
        self.assertFalse(any("x5" in t for t in texts))

    def test_labels_update_when_admin_changes_the_settings(self) -> None:
        markup = darts_side_kb(4.2, 2.1)
        texts = [btn.text for row in markup.inline_keyboard for btn in row]
        self.assertTrue(any("x4.2" in t for t in texts))
        self.assertTrue(any("x2.1" in t for t in texts))


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


class DartsBetFlowShowsMatchingCoefficientsTests(ChatModelsTestCase):
    async def test_side_selection_screen_uses_the_live_configured_coefficients(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=5, first_name="U", stars_balance=Decimal("100")))
            session.add(BotSettings(key="game_darts_coeff_bullseye", value="3.0"))
            session.add(BotSettings(key="game_darts_coeff_bounce", value="1.7"))
            await session.commit()

        state = _state()
        await state.set_state(GameStates.enter_bet)
        await state.update_data(game_type="darts", bet_step=1.0)
        message = SimpleNamespace(text="10", answer=AsyncMock())

        async with self.sessions() as session:
            db_user = await session.get(User, 5)
            await msg_bet_enter(message, session, db_user, state)

        args, kwargs = message.answer.await_args
        markup = kwargs["reply_markup"]
        texts = [btn.text for row in markup.inline_keyboard for btn in row]
        self.assertTrue(any("x3" in t for t in texts))
        self.assertTrue(any("x1.7" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
