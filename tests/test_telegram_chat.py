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
        self.assertIsNone(telegram_chat_id("https://t.me/c/1234567890/33"))

    def test_bot_username_is_never_treated_as_a_verifiable_chat(self) -> None:
        # get_chat_member only works on chats with a member list -- a bot
        # is never that, so callers must never get a chat_id back for one
        # (Telegram enforces every bot username ending in "bot").
        self.assertIsNone(telegram_chat_id("https://t.me/SomeSponsorBot"))
        self.assertIsNone(telegram_chat_id("t.me/some_sponsor_bot"))
        self.assertIsNone(telegram_chat_id("@SomeSponsorBot"))
        # A channel whose name merely contains "bot" mid-word is fine.
        self.assertEqual(telegram_chat_id("https://t.me/robotics_channel"), "@robotics_channel")

    def test_folder_invite_link_is_not_misidentified_as_a_channel(self) -> None:
        # t.me/addlist/<slug> is a Telegram *folder* invite, not a chat --
        # without the reserved-segment exclusion "addlist" would be read as
        # a literal channel username and passed to get_chat_member.
        self.assertIsNone(telegram_chat_id("https://t.me/addlist/AbCdEf123456"))
        self.assertIsNone(telegram_chat_id("t.me/addlist/AbCdEf123456"))

    def test_boost_link_is_not_misidentified_as_a_channel(self) -> None:
        # t.me/boost/<username> boosts a channel -- membership there isn't
        # the same as having given the boost, so this must resolve to None
        # (unverifiable via get_chat_member) rather than "@boost".
        self.assertIsNone(telegram_chat_id("https://t.me/boost/example_channel"))

    def test_restricted_non_member_is_not_subscribed(self) -> None:
        self.assertFalse(is_subscribed(SimpleNamespace(status="restricted", is_member=False)))
        self.assertTrue(is_subscribed(SimpleNamespace(status="restricted", is_member=True)))
        self.assertFalse(is_subscribed(SimpleNamespace(status="left")))
        self.assertTrue(is_subscribed(SimpleNamespace(status="member")))


if __name__ == "__main__":
    unittest.main()
