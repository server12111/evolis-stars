import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from bot.handlers.group.membership import _send_welcome


class SendWelcomeFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_ephemeral_success_sends_only_once(self) -> None:
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        await _send_welcome(bot, -1, 42)
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs.get("receiver_user_id"), 42)

    async def test_peer_id_invalid_falls_back_to_a_normal_message(self) -> None:
        """A brand-new member who's never interacted with the bot has no
        resolvable Telegram peer yet, so the ephemeral (receiver_user_id)
        send always fails with PEER_ID_INVALID -- confirmed live, every
        single join event hit this. The welcome must not be silently
        dropped; it must still reach the chat as a normal message."""
        error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: PEER_ID_INVALID")
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[error, SimpleNamespace(message_id=1)]))
        await _send_welcome(bot, -1, 42)

        self.assertEqual(bot.send_message.await_count, 2)
        first_call, second_call = bot.send_message.await_args_list
        self.assertEqual(first_call.kwargs.get("receiver_user_id"), 42)
        self.assertNotIn("receiver_user_id", second_call.kwargs)

    async def test_other_bad_request_does_not_retry(self) -> None:
        error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: CHAT_WRITE_FORBIDDEN")
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
        await _send_welcome(bot, -1, 42)
        bot.send_message.assert_awaited_once()

    async def test_fallback_failure_is_swallowed(self) -> None:
        peer_error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: PEER_ID_INVALID")
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[peer_error, Exception("network blip")]))
        await _send_welcome(bot, -1, 42)  # must not raise
        self.assertEqual(bot.send_message.await_count, 2)


if __name__ == "__main__":
    unittest.main()
