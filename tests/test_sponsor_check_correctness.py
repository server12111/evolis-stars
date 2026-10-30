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
    can legitimately rotate to a *different* batch later that just doesn't
    mention a saved sponsor anymore — that silent absence was being read
    as "subscribed", instead of independently re-verified.

    A provider batch that comes back completely EMPTY is re-verified the
    exact same way, not trusted outright — see
    ZeroOffersThreeWayVerificationTests below for that case and the
    live-exploit regression it fixes."""

    async def test_pin_rotated_to_other_offers_with_no_real_subscription_stays_pending(self) -> None:
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
            # Pin window expired / batch rotated to a DIFFERENT, still
            # non-empty set of offers — genuinely ambiguous whether
            # "expiredchan" was dropped because it's done or just because
            # the batch moved on.
            patch(
                "bot.services.botohub.check_botohub",
                AsyncMock(return_value=[{"name": "Other", "url": "https://example.com/other-offer"}]),
            ),
        ):
            # top_up=False -- isolate the reinstate decision itself from
            # the unrelated top-up feature, which would otherwise also
            # pull "other-offer" into the wave as a brand-new replacement
            # sponsor and make every assertion here ambiguous.
            wave_state = await _evaluate_wave_state(inner(), frozen_user(saved), session, bot, top_up=False)

        self.assertEqual(wave_state.status, "pending")
        self.assertEqual(len(wave_state.items), 1)
        self.assertEqual(wave_state.items[0]["url"], "https://t.me/expiredchan")
        bot.get_chat_member.assert_awaited_once()

    async def test_pin_rotated_to_other_offers_with_genuine_subscription_still_completes(self) -> None:
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
            patch(
                "bot.services.botohub.check_botohub",
                AsyncMock(return_value=[{"name": "Other", "url": "https://example.com/other-offer"}]),
            ),
        ):
            wave_state = await _evaluate_wave_state(inner(), frozen_user(saved), session, bot, top_up=False)

        self.assertEqual(wave_state.status, "complete")

    async def test_pin_rotated_to_other_offers_with_no_bot_access_resolves_complete(self) -> None:
        # The bot has no visibility into most third-party sponsor channels
        # (never added to them) — get_chat_member fails with "unknown", not
        # a definite "left" (see _reinstate_expired_pinned_sponsors' own
        # three-way True/False/None split). Confirmed live 2026-08-27: an
        # earlier version of this function reinstated on "unknown" here
        # (rotated batch, not just an empty one) same as on a definite
        # "left", which blocked real payouts for the common case (bot
        # genuinely can't tell) far more often than it ever caught fraud.
        # There's no way to distinguish an exploiter from a genuine
        # completion when the bot can't see into the chat at all, so the
        # provider's own verdict is trusted instead of guessed at --
        # exactly like an unverifiable (private/bot/web) sponsor already
        # was, and consistent with the empty-batch case (see
        # ZeroOffersThreeWayVerificationTests).
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
            patch(
                "bot.services.botohub.check_botohub",
                AsyncMock(return_value=[{"name": "Other", "url": "https://example.com/other-offer"}]),
            ),
        ):
            wave_state = await _evaluate_wave_state(inner(), frozen_user(saved), session, bot, top_up=False)

        self.assertEqual(wave_state.status, "complete")

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


class ZeroOffersThreeWayVerificationTests(unittest.IsolatedAsyncioTestCase):
    """Regression for the LIVE EXPLOIT reported 2026-08-27: users were
    trading a trick of subscribing to only 2-3 of 10-15 shown sponsors,
    then waiting out a provider's pin/rotation window for its offer batch
    to empty out entirely -- at which point every remaining
    never-subscribed sponsor silently resolved as done (an admin
    confirmed "yes, it counts them all"). Root cause: an entirely empty
    provider batch used to be trusted outright with no reinstate at all.

    A first fix (re-verify on empty batch same as a rotated one, bounded
    by a 3-hour grace timer so a channel the bot can never verify doesn't
    stay reinstated forever) closed the exploit but broke real payouts --
    confirmed live 2026-08-27, most sponsor channels return None (bot has
    no visibility into third-party channels, not "definitely not a
    member"), so nearly every legitimate completion got stuck behind the
    same 3-hour wait meant only for the rare unverifiable case.

    The actual fix distinguishes get_chat_member's three outcomes instead
    of lumping False and None together: True still resolves immediately,
    False (the exploiter's case -- a normal public channel they never
    joined) blocks permanently with no timer at all, and None (the
    common case -- can't tell either way) is trusted outright, exactly
    like an unverifiable private/bot/web sponsor already was."""

    async def test_definitely_not_subscribed_stays_pending_with_no_timeout(self) -> None:
        """The exploit case: a normal public channel the bot CAN check,
        and the user genuinely never joined -- must block for real, not
        just delay."""
        saved = [{"provider": "tgrass", "url": "https://t.me/MinePixels", "name": "Channel", "type": "tg"}]
        session = fake_session()
        bot = fake_bot(member_status="left")  # user genuinely never joined
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", ""),
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
        bot.get_chat_member.assert_awaited_once()

    async def test_cannot_verify_at_all_is_trusted_outright_with_no_wait(self) -> None:
        """The common case: the bot has no visibility into this channel
        (most third-party sponsors), same as it would for ANY check, not
        just an empty batch. Must resolve on the very first check -- no
        3-hour wait, no partial-trust workaround -- since there's no way
        to tell fraud from a genuine completion here regardless of how
        long we wait."""
        saved = [{"provider": "tgrass", "url": "https://t.me/MinePixels", "name": "Channel", "type": "tg"}]
        session = fake_session()
        bot = fake_bot_no_access()
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", ""),
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

    async def test_genuine_subscription_still_completes_immediately(self) -> None:
        saved = [{"provider": "tgrass", "url": "https://t.me/MinePixels", "name": "Channel", "type": "tg"}]
        session = fake_session()
        bot = fake_bot(member_status="member")
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", ""),
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

    async def test_stale_sponsor_check_press_skips_providers_when_already_verified(self) -> None:
        """Regression: a "sponsor_check" button never expires on an old
        Telegram message, so a verified user can press one long after
        passing. Unlike the periodic-recheck case above (sponsors_verified
        freshly flipped False), sponsors_verified is still True here --
        run_sponsor_wall_check must return True immediately without ever
        re-querying providers or re-freezing a wave, since a real provider
        (TGrass) has been observed flip-flopping status=ok/not_ok for the
        same user within seconds, which would otherwise randomly reopen a
        brand-new mandatory wall for someone who already got through."""
        session = fake_session()
        verified_user = user(sponsor_wave=3, sponsors_verified=True)
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", ""),
            patch("bot.services.tgrass.check_tgrass", AsyncMock()) as check_tgrass,
            patch("bot.services.botohub.check_botohub", AsyncMock()) as check_botohub,
        ):
            passed = await run_sponsor_wall_check(inner(), verified_user, session)

        self.assertTrue(passed)
        self.assertEqual(verified_user.sponsor_wave, 3)
        check_tgrass.assert_not_awaited()
        check_botohub.assert_not_awaited()

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
