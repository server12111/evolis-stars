import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database.models import Withdrawal
from bot.handlers.admin_channel import _update_public_withdrawal_status


class WithdrawalStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_updates_public_channel_message(self) -> None:
        callback = SimpleNamespace(bot=SimpleNamespace(edit_message_text=AsyncMock()))
        withdrawal = Withdrawal(
            id=12,
            user_id=345,
            amount=Decimal(15),
            channel_message_id=678,
        )
        user = SimpleNamespace(username="tester", first_name="Test", is_vip=False, referral_tier=None)

        with (
            patch(
                "bot.handlers.admin_channel.settings",
                SimpleNamespace(payments_channel_id="-100123"),
            ),
            patch(
                "bot.handlers.admin_channel.UserRepository.get",
                AsyncMock(return_value=user),
            ),
        ):
            await _update_public_withdrawal_status(
                callback,
                AsyncMock(),
                withdrawal,
                "Принято",
                "✅",
            )

        callback.bot.edit_message_text.assert_awaited_once()
        kwargs = callback.bot.edit_message_text.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100123)
        self.assertEqual(kwargs["message_id"], 678)
        self.assertIn("✅ Статус: <b>Принято</b>", kwargs["text"])
        self.assertNotIn("На рассмотрении", kwargs["text"])
        # amount=15 never asked for a method and RP⭐️ is an internal detail —
        # neither belongs in this admin-facing message.
        self.assertNotIn("Способ вывода", kwargs["text"])
        self.assertNotIn("RP⭐️", kwargs["text"])

    async def test_method_line_shown_only_at_or_above_the_choice_threshold(self) -> None:
        callback = SimpleNamespace(bot=SimpleNamespace(edit_message_text=AsyncMock()))
        withdrawal = Withdrawal(
            id=13,
            user_id=345,
            amount=Decimal(50),
            withdrawal_method="gift",
            channel_message_id=678,
        )
        user = SimpleNamespace(username="tester", first_name="Test", is_vip=False, referral_tier=None)

        with (
            patch(
                "bot.handlers.admin_channel.settings",
                SimpleNamespace(payments_channel_id="-100123"),
            ),
            patch(
                "bot.handlers.admin_channel.UserRepository.get",
                AsyncMock(return_value=user),
            ),
        ):
            await _update_public_withdrawal_status(
                callback, AsyncMock(), withdrawal, "Принято", "✅",
            )

        kwargs = callback.bot.edit_message_text.await_args.kwargs
        self.assertIn("Способ вывода", kwargs["text"])
        self.assertIn("Подарок", kwargs["text"])

    async def test_skips_when_public_message_was_not_created(self) -> None:
        callback = SimpleNamespace(bot=SimpleNamespace(edit_message_text=AsyncMock()))
        withdrawal = Withdrawal(
            id=12,
            user_id=345,
            amount=Decimal(15),
            channel_message_id=None,
        )

        await _update_public_withdrawal_status(
            callback,
            AsyncMock(),
            withdrawal,
            "Отклонено",
            "❌",
        )

        callback.bot.edit_message_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
