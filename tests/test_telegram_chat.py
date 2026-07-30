import unittest
from types import SimpleNamespace

from bot.services.telegram_chat import is_subscribed, telegram_chat_id


class TelegramChatTests(unittest.TestCase):
    def test_public_links_are_converted_for_bot_api(self) -> None:
        self.assertEqual(telegram_chat_id("https://t.me/example_channel"), "@example_channel")
        self.assertEqual(telegram_chat_id("t.me/example_channel/"), "@example_channel")
        self.assertEqual(telegram_chat_id("@example_channel"), "@example_channel")
        self.assertEqual(telegram_chat_id("-1001234567890"), -1001234567890)

    def test_private_invite_link_is_not_misidentified_as_chat(self) -> None:
        self.assertIsNone(telegram_chat_id("https://t.me/+privateInvite"))
        self.assertIsNone(telegram_chat_id("https://t.me/joinchat/privateInvite"))

    def test_restricted_non_member_is_not_subscribed(self) -> None:
        self.assertFalse(is_subscribed(SimpleNamespace(status="restricted", is_member=False)))
        self.assertTrue(is_subscribed(SimpleNamespace(status="restricted", is_member=True)))
        self.assertFalse(is_subscribed(SimpleNamespace(status="left")))
        self.assertTrue(is_subscribed(SimpleNamespace(status="member")))


if __name__ == "__main__":
    unittest.main()
