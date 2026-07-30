import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.services.country_notice import ensure_country_notice


class CountryNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_notice_is_blocked_until_phone_and_sponsors_are_verified(self) -> None:
        for phone_verified, sponsors_verified in (
            (False, False),
            (True, False),
            (False, True),
        ):
            user = SimpleNamespace(
                user_id=1,
                phone_verified=phone_verified,
                sponsors_verified=sponsors_verified,
                country_notice_message_id=None,
                country_notice_pinned=False,
            )
            bot = SimpleNamespace(
                send_message=AsyncMock(),
                pin_chat_message=AsyncMock(),
            )
            session = SimpleNamespace(commit=AsyncMock())

            await ensure_country_notice(user, session, bot)

            bot.send_message.assert_not_awaited()
            bot.pin_chat_message.assert_not_awaited()

    async def test_notice_is_sent_and_pinned_after_both_checks(self) -> None:
        user = SimpleNamespace(
            user_id=1,
            phone_verified=True,
            sponsors_verified=True,
            country_notice_message_id=None,
            country_notice_pinned=False,
        )
        bot = SimpleNamespace(
            send_message=AsyncMock(
                return_value=SimpleNamespace(message_id=42),
            ),
            pin_chat_message=AsyncMock(),
        )
        session = SimpleNamespace(commit=AsyncMock())

        await ensure_country_notice(user, session, bot)

        bot.send_message.assert_awaited_once()
        bot.pin_chat_message.assert_awaited_once_with(
            chat_id=1,
            message_id=42,
            disable_notification=True,
        )
        self.assertTrue(user.country_notice_pinned)


if __name__ == "__main__":
    unittest.main()
