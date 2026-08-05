import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin.games import _lottery_end_value
from bot.handlers.auction import cb_auction_bid
from bot.handlers.games import msg_bet_enter
from bot.keyboards.admin.settings import settings_kb
from bot.middlewares.user import UserMiddleware


class InputAndAdminSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_nan_bet_is_rejected_without_touching_database(self) -> None:
        message = SimpleNamespace(text="nan", answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={"game_type": "dice", "bet_step": 1.0}
            )
        )
        session = SimpleNamespace(commit=AsyncMock())
        user = SimpleNamespace(stars_balance=Decimal(100))

        await msg_bet_enter(message, session, user, state)

        session.commit.assert_not_awaited()
        message.answer.assert_awaited_once()

    async def test_removed_admin_id_revokes_persisted_admin_flag(self) -> None:
        user = SimpleNamespace(
            user_id=123,
            is_admin=True,
            is_blocked=False,
        )
        repository = SimpleNamespace(
            get_or_create=AsyncMock(return_value=(user, False, None))
        )
        session = SimpleNamespace(commit=AsyncMock())
        handler = AsyncMock(return_value="handled")
        data = {
            "session": session,
            "event_from_user": SimpleNamespace(
                id=123,
                username="user",
                first_name="User",
            ),
        }

        with (
            patch(
                "bot.middlewares.user.UserRepository",
                return_value=repository,
            ),
            patch(
                "bot.middlewares.user.settings",
                SimpleNamespace(admin_id_list=[]),
            ),
        ):
            result = await UserMiddleware()(handler, SimpleNamespace(), data)

        self.assertEqual(result, "handled")
        self.assertFalse(user.is_admin)
        session.commit.assert_awaited_once()
        handler.assert_awaited_once()

    async def test_financial_admin_settings_are_reachable(self) -> None:
        callbacks = {
            button.callback_data
            for row in settings_kb().inline_keyboard
            for button in row
            if button.callback_data
        }
        for key in (
            "rp_exchange_rate",
            "duel_commission",
            "duel_min_refs",
            "lottery_min_refs",
        ):
            self.assertIn(f"admin:settings_edit:{key}", callbacks)

    async def test_lottery_hours_are_converted_to_future_timestamp(self) -> None:
        before = datetime.utcnow().timestamp()
        result = _lottery_end_value("time", 2)
        after = datetime.utcnow().timestamp()
        self.assertGreaterEqual(result, before + 2 * 3600)
        self.assertLessEqual(result, after + 2 * 3600)

    async def test_micro_auction_increment_is_rejected_before_database_use(self) -> None:
        callback = SimpleNamespace(
            data="auction:bid:0.001",
            answer=AsyncMock(),
        )
        await cb_auction_bid(
            callback,
            SimpleNamespace(),
            SimpleNamespace(),
        )
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])


if __name__ == "__main__":
    unittest.main()
