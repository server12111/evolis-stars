import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.middlewares.sponsor_wall import run_sponsor_wall_check, settings


def offers(prefix: str, count: int) -> list[dict]:
    return [
        {"name": f"Channel {i}", "url": f"https://t.me/{prefix}{i}"}
        for i in range(count)
    ]


def inner() -> SimpleNamespace:
    return SimpleNamespace(from_user=None, message=None, answer=AsyncMock())


def user(**kwargs) -> SimpleNamespace:
    base = dict(
        user_id=1,
        sponsor_wave=0,
        sponsor_wave_one=None,
        sponsor_wave_two=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def fake_get_int(min_sponsors: int, wave_size: int = 10):
    async def _get_int(self, key: str, default: int = 0) -> int:
        return {
            "min_sponsors_for_reward": min_sponsors,
            "sponsor_max_channels": wave_size,
        }.get(key, default)

    return _get_int


def fake_bot(member_status: str = "member") -> SimpleNamespace:
    return SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=member_status)))


class SponsorCheckIndependentVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_false_negative_is_overridden_by_our_own_check(self) -> None:
        """The provider says the user is still unsubscribed, but our own bot
        confirms they ARE a member — the false negative must be corrected
        instead of blocking the user."""
        session = SimpleNamespace(commit=AsyncMock())
        bot = fake_bot(member_status="member")
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 1))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            passed = await run_sponsor_wall_check(inner(), user(), session, bot)

        self.assertTrue(passed)
        bot.get_chat_member.assert_awaited_once()

    async def test_genuinely_unsubscribed_user_still_blocked(self) -> None:
        """Our own check confirms the provider was right (user really isn't
        a member) — the wave must still show as pending."""
        session = SimpleNamespace(commit=AsyncMock())
        bot = fake_bot(member_status="left")
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 1))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            passed = await run_sponsor_wall_check(inner(), user(), session, bot)

        self.assertFalse(passed)

    async def test_no_bot_available_falls_back_to_trusting_the_provider(self) -> None:
        """Backward-compatible: when bot isn't passed, behave exactly as
        before — trust the provider's unsubscribed list outright."""
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 1))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            passed = await run_sponsor_wall_check(inner(), user(), session)

        self.assertFalse(passed)

    async def test_get_chat_member_failure_falls_back_to_trusting_the_provider(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=Exception("boom")))
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 1))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            passed = await run_sponsor_wall_check(inner(), user(), session, bot)

        self.assertFalse(passed)


if __name__ == "__main__":
    unittest.main()
