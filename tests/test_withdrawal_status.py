import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database.models import Withdrawal
from bot.handlers.admin.users import _update_public_withdrawal_status


class WithdrawalStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_updates_public_channel_message(self) -> None:
        callback = SimpleNamespace(bot=SimpleNamespace(edit_message_text=AsyncMock()))
        withdrawal = Withdrawal(
            id=12,
            user_id=345,
            amount=Decimal(15),
            channel_message_id=678,
        )
        user = SimpleNamespace(username="tester", first_name="Test")

        with (
            patch(
                "bot.handlers.admin.users.settings",
                SimpleNamespace(payments_channel_id="-100123"),
            ),
            patch(
                "bot.handlers.admin.users.UserRepository.get",
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
