import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.middlewares.sponsor_wall import _evaluate_wave_state, run_sponsor_wall_check, settings


def offers(prefix: str, count: int) -> list[dict]:
    return [
        {"name": f"Channel {i}", "url": f"https://t.me/{prefix}{i}"}
        for i in range(count)
    ]


def inner() -> SimpleNamespace:
    return SimpleNamespace(from_user=None, message=None, answer=AsyncMock())


def fake_session() -> SimpleNamespace:
    """Minimal fake covering both the direct commit() calls this suite
    already exercised and BlockedSponsorRepository's SELECT (via
    _evaluate_wave_state's blocklist lookup) -- defaults to an empty
    blocklist so existing scenarios are unaffected."""
    return SimpleNamespace(
        commit=AsyncMock(),
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))),
    )


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


def fake_bot_no_access() -> SimpleNamespace:
    """Most sponsor channels are third-party — this bot was never added to
    them, so get_chat_member raises instead of returning a real status."""
    return SimpleNamespace(get_chat_member=AsyncMock(side_effect=Exception("member list is inaccessible")))


class SponsorCheckIndependentVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_false_negative_is_overridden_by_our_own_check(self) -> None:
        """The provider says the user is still unsubscribed, but our own bot
        confirms they ARE a member — the false negative must be corrected
        instead of blocking the user."""
        session = fake_session()
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
        session = fake_session()
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
        session = fake_session()
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
        session = fake_session()
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


def frozen_user(items: list[dict], **kwargs) -> SimpleNamespace:
    base = dict(
        user_id=1,
        sponsor_wave=1,
        sponsor_wave_one=json.dumps(items),
        sponsor_wave_two=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class ExpiredPinnedSponsorTests(unittest.IsolatedAsyncioTestCase):
    """Item: a user pressed "check subscription" without actually joining
    anything and was told they'd subscribed to every sponsor. Root cause:
    BotoHub/tgrass pin a sponsor batch to a user for a limited window and
    can legitimately return an empty/rotated batch later — that emptiness
    was being read as "subscribed" for sponsors already shown to the user,
    instead of independently re-verified."""

    async def test_expired_pin_with_no_real_subscription_stays_pending(self) -> None:
        saved = [{"provider": "botohub", "url": "https://t.me/expiredchan", "name": "Channel", "type": "tg"}]
        session = fake_session()
        bot = fake_bot(member_status="left")  # user genuinely never joined
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            # Pin window expired / batch rotated — provider now reports
            # nothing pending for this user, even though they never joined.
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            wave_state = await _evaluate_wave_state(inner(), frozen_user(saved), session, bot)

        self.assertEqual(wave_state.status, "pending")
        self.assertEqual(len(wave_state.items), 1)
        bot.get_chat_member.assert_awaited_once()

    async def test_expired_pin_with_genuine_subscription_still_completes(self) -> None:
        saved = [{"provider": "botohub", "url": "https://t.me/joinedchan", "name": "Channel", "type": "tg"}]
        session = fake_session()
        bot = fake_bot(member_status="member")  # user genuinely did join
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            wave_state = await _evaluate_wave_state(inner(), frozen_user(saved), session, bot)

        self.assertEqual(wave_state.status, "complete")

    async def test_expired_pin_with_no_bot_access_still_stays_pending(self) -> None:
        # The bot has no visibility into most third-party sponsor channels
        # (never added to them) — get_chat_member fails with "unknown", not
        # a definite "left". That must NOT be read as "fine, drop it" just
        # because the provider's rotating batch stopped mentioning the
        # sponsor this cycle, or a user could clear a requirement they
        # never actually subscribed to just by waiting out the pin window.
        saved = [{"provider": "botohub", "url": "https://t.me/expiredchan", "name": "Channel", "type": "tg"}]
        session = fake_session()
        bot = fake_bot_no_access()
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            wave_state = await _evaluate_wave_state(inner(), frozen_user(saved), session, bot)

        self.assertEqual(wave_state.status, "pending")
        self.assertEqual(len(wave_state.items), 1)

    async def test_bot_type_sponsor_trusts_the_provider_since_it_cant_be_verified(self) -> None:
        # A sponsor that's a Telegram BOT (not a channel/group) can never
        # be checked via get_chat_member -- that call only works on chats
        # with a member list, so it always fails for a bot regardless of
        # whether the user actually started it. Unlike the channel case
        # above, the provider dropping it from its report IS the only
        # signal available and must be trusted, or a bot sponsor the user
        # genuinely completed would get reinstated forever.
        saved = [{"provider": "botohub", "url": "https://t.me/SomeSponsorBot", "name": "Bot", "type": "tg"}]
        session = fake_session()
        bot = fake_bot_no_access()
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            wave_state = await _evaluate_wave_state(inner(), frozen_user(saved), session, bot)

        self.assertEqual(wave_state.status, "complete")
        bot.get_chat_member.assert_not_awaited()

    async def test_provider_still_reporting_it_skips_the_extra_reinstate_check(self) -> None:
        # No expiry involved here — the provider already correctly reports
        # the sponsor as unsubscribed, so _reinstate_expired_pinned_sponsors
        # has nothing to do (the one get_chat_member call that does happen
        # is _drop_confirmed_subscriptions' own false-negative check).
        saved = [{"provider": "botohub", "url": "https://t.me/stillpending", "name": "Channel", "type": "tg"}]
        session = fake_session()
        bot = fake_bot(member_status="left")
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch(
                "bot.services.botohub.check_botohub",
                AsyncMock(return_value=[{"name": "Channel", "url": "https://t.me/stillpending"}]),
            ),
        ):
            wave_state = await _evaluate_wave_state(inner(), frozen_user(saved), session, bot)

        self.assertEqual(wave_state.status, "pending")
        bot.get_chat_member.assert_awaited_once()


class SponsorRecheckAfterCompleteTests(unittest.IsolatedAsyncioTestCase):
    """sponsor_recheck_scheduler periodically flips sponsors_verified back
    to False for already-complete (wave==3) users specifically so the
    next interaction re-checks providers for sponsors that weren't there
    before. The wave==3 shortcut must only apply to the sponsor_skip
    path, never to this general recheck path — otherwise the recheck
    scheduler becomes a permanent no-op the moment a user first reaches
    wave 3."""

    async def test_periodic_recheck_offers_a_new_sponsor_after_wave_3(self) -> None:
        session = fake_session()
        complete_user = user(sponsor_wave=3)
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=1),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("new", 1))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            passed = await run_sponsor_wall_check(inner(), complete_user, session)

        # A new sponsor wave is shown, not silently passed through.
        self.assertFalse(passed)
        self.assertEqual(complete_user.sponsor_wave, 1)

    async def test_sponsor_skip_still_skips_provider_calls_for_wave_3(self) -> None:
        """The stale-button performance guard must still hold — sponsor_
        skip must never re-query providers for an already-complete user."""
        from bot.middlewares.sponsor_wall import get_pending_sponsor_items

        session = fake_session()
        complete_user = user(sponsor_wave=3)
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch("bot.services.tgrass.check_tgrass", AsyncMock()) as check_tgrass,
            patch("bot.services.botohub.check_botohub", AsyncMock()) as check_botohub,
        ):
            items = await get_pending_sponsor_items(inner(), complete_user, session)

        self.assertEqual(items, [])
        check_tgrass.assert_not_awaited()
        check_botohub.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
