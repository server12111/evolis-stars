import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BotSettings, ReferralReactivation, User
from bot.services.referral import (
    check_referral_reward,
    get_referral_reward,
    resolve_pending_reactivation,
    reward_returning_referral,
    trigger_referral_reactivation_wall,
)


def _wave_json(*urls: str) -> str:
    import json
    return json.dumps([{"url": url, "provider": "tgrass", "name": "Sponsor"} for url in urls])


def _bot(confirmed_statuses: dict[str, str] | None = None) -> SimpleNamespace:
    """A fake Bot whose get_chat_member confirms membership per chat_id
    (keyed by the @username telegram_chat_id() resolves each t.me URL to),
    defaulting to a confirmed member for anything not listed."""
    statuses = confirmed_statuses or {}

    async def get_chat_member(chat_id, user_id):
        return SimpleNamespace(status=statuses.get(chat_id, "member"))

    return SimpleNamespace(get_chat_member=AsyncMock(side_effect=get_chat_member), send_message=AsyncMock())


class ReferralRewardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_referral_reward_rounds_to_stars_precision(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward_4_5", value="4.005"))
            await session.commit()
            reward = await get_referral_reward(session, 5)
        self.assertEqual(reward, Decimal("4.01"))

    async def test_same_return_cycle_is_rewarded_only_once(self) -> None:
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            referrer = User(user_id=700, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=701,
                first_name="Referred",
                referrer_id=700,
                referral_counted=True,
                referral_reward_given=True,
                last_seen_at=inactive_since,
                sponsor_wave_one=_wave_json(
                    "https://t.me/a", "https://t.me/b", "https://t.me/c", "https://t.me/d", "https://t.me/e"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            first = await reward_returning_referral(
                referred, referrer.user_id, inactive_since, session,
            )
            second = await reward_returning_referral(
                referred, referrer.user_id, inactive_since, session,
            )

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 700)
            ledger_count = (
                await session.execute(select(func.count(ReferralReactivation.id)))
            ).scalar_one()

        # 4-5 sponsor tier (6 RP⭐️), halved.
        self.assertEqual(first, Decimal("3.00"))
        self.assertIsNone(second)
        self.assertEqual(float(saved_referrer.stars_balance), 3.0)
        self.assertEqual(ledger_count, 1)

    async def test_reactivation_pays_nothing_below_the_minimum_sponsors(self) -> None:
        """The bug being fixed: reward_returning_referral used to pay
        regardless of how many sponsors (even zero) the returning user
        currently has -- it must now respect min_sponsors_for_reward just
        like the normal check_referral_reward path does."""
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            referrer = User(user_id=702, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=703,
                first_name="Referred",
                referrer_id=702,
                referral_counted=True,
                referral_reward_given=True,
                last_seen_at=inactive_since,
                # Only 1 sponsor -- below the default minimum of 3.
                sponsor_wave_one=_wave_json("https://t.me/only-one"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            reward = await reward_returning_referral(
                referred, referrer.user_id, inactive_since, session,
            )

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 702)
            ledger_count = (
                await session.execute(select(func.count(ReferralReactivation.id)))
            ).scalar_one()

        self.assertIsNone(reward)
        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertEqual(ledger_count, 0)

    async def test_returning_user_is_notified_when_below_the_minimum(self) -> None:
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            referrer = User(user_id=704, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=705,
                first_name="Referred",
                referrer_id=704,
                referral_counted=True,
                referral_reward_given=True,
                last_seen_at=inactive_since,
                sponsor_wave_one=_wave_json("https://t.me/only-one"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot()
            reward = await reward_returning_referral(
                referred, referrer.user_id, inactive_since, session, bot,
            )

        self.assertIsNone(reward)
        self.assertEqual(bot.send_message.await_count, 2)
        notified_ids = {call.args[0] for call in bot.send_message.await_args_list}
        self.assertEqual(notified_ids, {704, 705})  # both the referrer and the returning user

    async def test_ordinary_referral_reward_is_paid_once_and_marks_counted(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=710, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=711,
                first_name="Referred",
                referrer_id=710,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(
                    "https://t.me/a", "https://t.me/b", "https://t.me/c", "https://t.me/d", "https://t.me/e"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)
            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 710)
            saved_referred = await session.get(User, 711)

        # 4-5 sponsor tier reward (6 RP⭐️), paid exactly once regardless of
        # how many times check_referral_reward is called afterwards.
        self.assertEqual(float(saved_referrer.stars_balance), 6.0)
        self.assertEqual(saved_referrer.referrals_count, 1)
        self.assertTrue(saved_referred.referral_reward_given)
        self.assertTrue(saved_referred.referral_counted)

    async def test_insufficient_sponsors_notifies_once_then_pays_once_threshold_met(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=720, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=721,
                first_name="Referred",
                referrer_id=720,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json("https://t.me/a"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot()
            await check_referral_reward(referred, session, bot)
            await check_referral_reward(referred, session, bot)  # same single sponsor, still short

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 720)
            saved_referred = await session.get(User, 721)

        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertEqual(saved_referrer.referrals_count, 0)
        self.assertFalse(saved_referred.referral_reward_given)
        self.assertTrue(saved_referred.referral_insufficient_notified)
        # Notified once each — the referrer (reward withheld) and the
        # referred user (told why their friend wasn't paid) — and not
        # again on the second check_referral_reward call above.
        self.assertEqual(bot.send_message.await_count, 2)
        notified_ids = {call.args[0] for call in bot.send_message.await_args_list}
        self.assertEqual(notified_ids, {720, 721})

        # Once the referred user reaches 5 sponsors, the payout goes
        # through — the 4-5 sponsor tier reward.
        async with self.sessions() as session:
            saved_referred = await session.get(User, 721)
            saved_referred.sponsor_wave_two = _wave_json(
                "https://t.me/b", "https://t.me/c", "https://t.me/d", "https://t.me/e"
            )
            await session.commit()
            await check_referral_reward(saved_referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 720)
            saved_referred = await session.get(User, 721)

        self.assertEqual(float(saved_referrer.stars_balance), 6.0)
        self.assertEqual(saved_referrer.referrals_count, 1)
        self.assertTrue(saved_referred.referral_reward_given)

    async def test_new_sponsors_after_payout_are_never_paid_again(self) -> None:
        """Reversion of the earlier 'recurring reward' behavior: once a
        referral has been rewarded, gaining new sponsors later must not
        trigger another payout."""
        async with self.sessions() as session:
            referrer = User(user_id=730, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=731,
                first_name="Referred",
                referrer_id=730,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(*[f"https://t.me/tg{i}" for i in range(5)]),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 730)
        self.assertEqual(float(saved_referrer.stars_balance), 6.0)
        self.assertEqual(saved_referrer.referrals_count, 1)

        # Simulate the recheck scheduler unlocking brand-new sponsors after
        # the reward has already been paid.
        async with self.sessions() as session:
            saved_referred = await session.get(User, 731)
            saved_referred.sponsor_wave_two = _wave_json("https://t.me/new1", "https://t.me/new2")
            await session.commit()
            await check_referral_reward(saved_referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 730)

        # No second payout, no second milestone increment.
        self.assertEqual(float(saved_referrer.stars_balance), 6.0)
        self.assertEqual(saved_referrer.referrals_count, 1)

    async def test_returning_referral_reward_uses_fresh_count_not_stale_wave(self) -> None:
        """Item: a returning referral subscribed to just 5 sponsors on this
        visit, but the reactivation reward priced it as if for 6-7 —
        because it was reading sponsor_count straight off the frozen wave
        saved back when this referral FIRST completed the sponsor wall
        (months ago), with no re-verification that those sponsors are
        still current."""
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            referrer = User(user_id=770, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=771,
                first_name="Referred",
                referrer_id=770,
                referral_counted=True,
                referral_reward_given=True,
                last_seen_at=inactive_since,
                # A big first-time wave from long ago — 7 sponsors.
                sponsor_wave_one=_wave_json(*[f"https://t.me/old{i}" for i in range(7)]),
            )
            session.add_all((referrer, referred))
            await session.commit()

            # Bot-confirmed: the user has since left 2 of the old sponsors —
            # only 5 are genuinely current right now (still clears the
            # min-sponsors floor).
            bot = _bot(confirmed_statuses={f"@old{i}": "left" for i in range(5, 7)})
            reward = await reward_returning_referral(
                referred, referrer.user_id, inactive_since, session, bot,
            )

        # The 4-5 tier (referral_reward_4_5, 6), halved for reactivation ->
        # 3.00. Must NOT be the 6-7 tier (referral_reward_6_7, 9 -> 4.50)
        # the stale 7-item wave would have produced.
        self.assertEqual(reward, Decimal("3.00"))

    async def test_bot_side_verification_excludes_unconfirmed_tg_sponsor(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=740, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=741,
                first_name="Referred",
                referrer_id=740,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(
                    "https://t.me/good1", "https://t.me/good2", "https://t.me/good3", "https://t.me/fake"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot(confirmed_statuses={"@fake": "left"})
            await check_referral_reward(referred, session, bot)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 740)
            saved_referred = await session.get(User, 741)

        # Must use the CONFIRMED count (3, the 0-3 tier -- pays 0, treated
        # as insufficient, never counted) rather than the gross count (4,
        # the 4-5 tier, which would pay 6 RP⭐️ and count the referral).
        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertFalse(saved_referred.referral_reward_given)

    async def test_unconfirmed_sponsor_can_drop_total_below_minimum(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=750, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=751,
                first_name="Referred",
                referrer_id=750,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(
                    "https://t.me/good1", "https://t.me/good2", "https://t.me/fake"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot(confirmed_statuses={"@fake": "left"})
            await check_referral_reward(referred, session, bot)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 750)
            saved_referred = await session.get(User, 751)

        # Only 2 of 3 sponsors are confirmed — below the minimum of 3 — so
        # no payout happens even though the bot never goes negative for it.
        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertFalse(saved_referred.referral_reward_given)

    async def test_exactly_3_sponsors_is_not_counted_despite_passing_the_raw_gate(self) -> None:
        """Regression: 3 sponsors clears the raw min_sponsors_for_reward
        gate (default 3) but lands in the 0-3 reward tier, which pays 0
        RP⭐️ by design. Must be treated exactly like "insufficient
        sponsors" -- NOT silently counted (referral_reward_given=True) for
        a zero payout, which would permanently waste the referral and show
        the referrer a misleading "credited 0 RP" success message instead
        of ever getting a real reward for this referral later."""
        async with self.sessions() as session:
            referrer = User(user_id=752, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=753,
                first_name="Referred",
                referrer_id=752,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot()
            await check_referral_reward(referred, session, bot)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 752)
            saved_referred = await session.get(User, 753)

        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertFalse(saved_referred.referral_reward_given)
        self.assertEqual(saved_referrer.referrals_count, 0)
        # Both parties get the "insufficient sponsors" notice, not a
        # misleading "credited 0 RP" success message.
        self.assertEqual(bot.send_message.await_count, 2)
        notified_ids = {call.args[0] for call in bot.send_message.await_args_list}
        self.assertEqual(notified_ids, {752, 753})
        for call in bot.send_message.await_args_list:
            self.assertIn("недостаточно", call.args[1].lower())
            self.assertNotIn("вам начислено", call.args[1].lower())

    async def test_milestone_bonus_and_vip_apply_on_top_of_flat_reward(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_bonus_10", value="0.1"))
            await session.commit()
            referrer = User(
                user_id=760,
                first_name="Referrer",
                stars_balance=Decimal(0),
                referrals_count=9,
            )
            referred = User(
                user_id=761,
                first_name="Referred",
                referrer_id=760,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(
                    "https://t.me/a", "https://t.me/b", "https://t.me/c", "https://t.me/d", "https://t.me/e"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 760)

        # 4-5 sponsor tier reward (6) + the 10-referral milestone bonus (0.1).
        self.assertEqual(float(saved_referrer.stars_balance), 6.1)
        self.assertEqual(saved_referrer.referrals_count, 10)

    async def test_100_is_a_one_time_milestone_not_an_infinite_recurring_bonus(self) -> None:
        """RECURRING_MILESTONE (100+ paid forever) was removed -- 100 is now
        an ordinary one-time milestone and referral #101 must NOT keep
        earning that bonus."""
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_bonus_100", value="1"))
            await session.commit()
            referrer = User(
                user_id=780, first_name="Referrer", stars_balance=Decimal(0), referrals_count=99,
            )
            referred = User(
                user_id=781, first_name="Referred", referrer_id=780, sponsors_verified=True,
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c", "https://t.me/d", "https://t.me/e"),
            )
            session.add_all((referrer, referred))
            await session.commit()
            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 780)
        # 99 -> 100 hits the one-time milestone: 4-5 tier reward (6) + 1.
        self.assertEqual(float(saved_referrer.stars_balance), 7.0)
        self.assertEqual(saved_referrer.referrals_count, 100)

        async with self.sessions() as session:
            referred_2 = User(
                user_id=782, first_name="Referred2", referrer_id=780, sponsors_verified=True,
                sponsor_wave_one=_wave_json("https://t.me/f", "https://t.me/g", "https://t.me/h", "https://t.me/i", "https://t.me/j"),
            )
            session.add(referred_2)
            await session.commit()
            await check_referral_reward(referred_2, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 780)
        # 101st referral is below the new 200+ recurring floor: only the
        # 4-5 tier reward (6), no milestone/recurring bonus on top.
        self.assertEqual(float(saved_referrer.stars_balance), 13.0)
        self.assertEqual(saved_referrer.referrals_count, 101)

    async def test_vip_flag_flips_silently_with_no_special_message(self) -> None:
        async with self.sessions() as session:
            referrer = User(
                user_id=790, first_name="Referrer", stars_balance=Decimal(0), referrals_count=49,
            )
            referred = User(
                user_id=791, first_name="Referred", referrer_id=790, sponsors_verified=True,
                # 4, not 3 -- the 0-3 tier pays 0 RP⭐️ and would never
                # actually count this referral at all.
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c", "https://t.me/d"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot()
            await check_referral_reward(referred, session, bot)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 790)

        self.assertTrue(saved_referrer.is_vip)
        bot.send_message.assert_awaited_once()
        sent_text = bot.send_message.await_args.args[1]
        self.assertNotIn("VIP", sent_text)

    async def test_concurrent_referral_completions_do_not_lose_a_count(self) -> None:
        """A concurrent referral for the same referrer landing in between
        our own referrer read and our guarded update must not get
        silently overwritten — the WHERE-guarded update should miss and
        retry against the fresh value instead."""
        async with self.sessions() as session:
            referrer = User(
                user_id=800, first_name="Referrer", stars_balance=Decimal(0), referrals_count=0,
            )
            referred = User(
                user_id=801, first_name="Referred", referrer_id=800, sponsors_verified=True,
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            # get_referral_reward() is awaited right after the initial
            # referrer read and right before the retry loop's guarded
            # update — the perfect seam to inject a "concurrent" referral
            # that commits in between, using the exact same kind of atomic
            # update the retry loop itself uses (no nested session needed).
            async def sneaky_get_referral_reward(*args, **kwargs):
                await session.execute(
                    update(User)
                    .where(User.user_id == 800)
                    .values(referrals_count=User.referrals_count + 1)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
                return Decimal("4")

            with patch(
                "bot.services.referral.get_referral_reward",
                side_effect=sneaky_get_referral_reward,
            ):
                await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 800)
            saved_referred = await session.get(User, 801)

        # The concurrent +1 landed first (count=1); our own referral's
        # first attempt (WHERE referrals_count == 0) can't match anymore,
        # so it must retry against the fresh value and land on 2 — not
        # silently overwrite it back down to 1.
        self.assertEqual(saved_referrer.referrals_count, 2)
        self.assertTrue(saved_referred.referral_reward_given)


class ReactivationWallTests(unittest.IsolatedAsyncioTestCase):
    """trigger_referral_reactivation_wall / resolve_pending_reactivation:
    the fresh-sponsor-wall fix for returning referrals."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _eligible_returning_user(self, referrer_id: int, referred_id: int, **overrides) -> tuple[datetime, User, User]:
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            referrer = User(user_id=referrer_id, first_name="Referrer", stars_balance=Decimal(0))
            referred_kwargs = dict(
                user_id=referred_id,
                first_name="Referred",
                referrer_id=referrer_id,
                referral_counted=True,
                referral_reward_given=True,
                last_seen_at=inactive_since,
                sponsors_verified=True,
                sponsor_wave=3,
                sponsor_wave_one=_wave_json("https://t.me/old-a", "https://t.me/old-b", "https://t.me/old-c"),
            )
            referred_kwargs.update(overrides)
            referred = User(**referred_kwargs)
            session.add_all((referrer, referred))
            await session.commit()
        return inactive_since, referrer, referred

    async def test_trigger_resets_wave_and_blocks_access(self) -> None:
        inactive_since, referrer, referred = await self._eligible_returning_user(810, 811)
        async with self.sessions() as session:
            user = await session.get(User, 811)
            triggered = await trigger_referral_reactivation_wall(user, 810, inactive_since, session)

        async with self.sessions() as session:
            saved = await session.get(User, 811)

        self.assertTrue(triggered)
        self.assertFalse(saved.sponsors_verified)
        self.assertEqual(saved.sponsor_wave, 0)
        self.assertIsNone(saved.sponsor_wave_one)
        self.assertIsNone(saved.sponsor_wave_two)
        self.assertEqual(saved.pending_reactivation_referrer_id, 810)
        self.assertEqual(saved.pending_reactivation_since, inactive_since)

    async def test_trigger_is_a_no_op_when_not_inactive_long_enough(self) -> None:
        recent = datetime.utcnow() - timedelta(days=1)
        _, referrer, referred = await self._eligible_returning_user(820, 821, last_seen_at=recent)
        async with self.sessions() as session:
            user = await session.get(User, 821)
            triggered = await trigger_referral_reactivation_wall(user, 820, recent, session)

        async with self.sessions() as session:
            saved = await session.get(User, 821)

        self.assertFalse(triggered)
        self.assertTrue(saved.sponsors_verified)
        self.assertIsNone(saved.pending_reactivation_referrer_id)

    async def test_trigger_is_a_no_op_for_wrong_referrer(self) -> None:
        inactive_since, referrer, referred = await self._eligible_returning_user(830, 831)
        async with self.sessions() as session:
            user = await session.get(User, 831)
            triggered = await trigger_referral_reactivation_wall(user, 999999, inactive_since, session)
        self.assertFalse(triggered)

    async def test_trigger_is_a_no_op_when_not_previously_rewarded(self) -> None:
        inactive_since, referrer, referred = await self._eligible_returning_user(
            840, 841, referral_reward_given=False,
        )
        async with self.sessions() as session:
            user = await session.get(User, 841)
            triggered = await trigger_referral_reactivation_wall(user, 840, inactive_since, session)
        self.assertFalse(triggered)

    async def test_trigger_does_not_reset_an_already_pending_reactivation(self) -> None:
        inactive_since, referrer, referred = await self._eligible_returning_user(850, 851)
        async with self.sessions() as session:
            user = await session.get(User, 851)
            user.pending_reactivation_referrer_id = 850
            user.pending_reactivation_since = inactive_since
            user.sponsors_verified = False
            user.sponsor_wave = 0
            user.sponsor_wave_one = None
            await session.commit()
            # A second click of the same link shouldn't re-trigger while
            # the first reactivation wall is still in progress.
            triggered = await trigger_referral_reactivation_wall(user, 850, inactive_since, session)
        self.assertFalse(triggered)

    async def test_trigger_is_a_no_op_when_a_wave_is_genuinely_mid_progress(self) -> None:
        """A user stuck mid some other (non-reactivation) wall -- a wave is
        actually frozen (sponsor_wave 1/2) right now -- must not also get
        force-reset into a reactivation wall on top of it."""
        inactive_since, referrer, referred = await self._eligible_returning_user(
            860, 861, sponsors_verified=False, sponsor_wave=1,
        )
        async with self.sessions() as session:
            user = await session.get(User, 861)
            triggered = await trigger_referral_reactivation_wall(user, 860, inactive_since, session)
        self.assertFalse(triggered)

    async def test_trigger_fires_even_when_the_recheck_scheduler_flipped_verified_false(self) -> None:
        """Regression: sponsor_recheck_loop (bot/services/
        sponsor_recheck_scheduler.py) flips sponsors_verified back to False
        for EVERY verified user every ~10 minutes, regardless of activity --
        a referral inactive for REFERRAL_RETURN_DAYS is essentially
        guaranteed to already have sponsors_verified=False by the time they
        click their link again. The trigger must not gate on that flag
        (sponsor_wave==3, i.e. "already fully resolved," is the correct
        signal that nothing is genuinely mid-progress)."""
        inactive_since, referrer, referred = await self._eligible_returning_user(
            862, 863, sponsors_verified=False, sponsor_wave=3,
        )
        async with self.sessions() as session:
            user = await session.get(User, 863)
            triggered = await trigger_referral_reactivation_wall(user, 862, inactive_since, session)
        self.assertTrue(triggered)

    async def test_resolve_is_a_no_op_when_nothing_pending(self) -> None:
        async with self.sessions() as session:
            user = User(user_id=870, first_name="U", sponsors_verified=True)
            session.add(user)
            await session.commit()
            await resolve_pending_reactivation(user, session)  # must not raise
        async with self.sessions() as session:
            saved = await session.get(User, 870)
        self.assertIsNone(saved.pending_reactivation_referrer_id)

    async def test_full_cycle_pays_referrer_using_the_fresh_wave(self) -> None:
        """trigger -> (simulated fresh wall clears) -> resolve pays the
        referrer off the NEW wave, and clears the pending markers."""
        inactive_since, referrer, referred = await self._eligible_returning_user(880, 881)
        async with self.sessions() as session:
            user = await session.get(User, 881)
            self.assertTrue(await trigger_referral_reactivation_wall(user, 880, inactive_since, session))

        # Simulate the fresh sponsor wall clearing (what run_sponsor_wall_check
        # + _proceed_after_tos would do): a brand-new wave, now subscribed.
        async with self.sessions() as session:
            user = await session.get(User, 881)
            user.sponsors_verified = True
            user.sponsor_wave = 3
            user.sponsor_wave_one = _wave_json(
                "https://t.me/new-a", "https://t.me/new-b", "https://t.me/new-c",
                "https://t.me/new-d", "https://t.me/new-e",
            )
            await session.commit()
            await resolve_pending_reactivation(user, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 880)
            saved_referred = await session.get(User, 881)
            ledger_count = (
                await session.execute(select(func.count(ReferralReactivation.id)))
            ).scalar_one()

        self.assertEqual(float(saved_referrer.stars_balance), 3.0)  # 6 (4-5 sponsor tier) / 2
        self.assertIsNone(saved_referred.pending_reactivation_referrer_id)
        self.assertIsNone(saved_referred.pending_reactivation_since)
        self.assertEqual(ledger_count, 1)

    async def test_full_cycle_pays_nothing_if_fresh_wave_ends_up_below_minimum(self) -> None:
        inactive_since, referrer, referred = await self._eligible_returning_user(890, 891)
        async with self.sessions() as session:
            user = await session.get(User, 891)
            self.assertTrue(await trigger_referral_reactivation_wall(user, 890, inactive_since, session))

        async with self.sessions() as session:
            user = await session.get(User, 891)
            user.sponsors_verified = True
            user.sponsor_wave = 3
            # Only 1 sponsor in the fresh wave -- below the minimum.
            user.sponsor_wave_one = _wave_json("https://t.me/new-only")
            await session.commit()
            await resolve_pending_reactivation(user, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 890)
            saved_referred = await session.get(User, 891)

        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertIsNone(saved_referred.pending_reactivation_referrer_id)

    async def test_cmd_start_does_not_arm_a_wall_with_no_sponsor_provider_configured(self) -> None:
        """A wall must only be armed if one can genuinely be shown --
        otherwise _proceed_after_tos skips straight to sponsors_verified=
        True (no providers to check against), the wave self-resolves
        against the just-wiped empty waves, and the referrer's reward off
        the pre-existing (stale but nonzero) wave is lost for nothing."""
        from bot.handlers.start import cmd_start

        inactive_since, referrer, referred = await self._eligible_returning_user(
            900, 901, tos_accepted=True,
        )
        message = SimpleNamespace(
            text="/start ref_900", answer=AsyncMock(), answer_photo=AsyncMock(),
        )
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), clear=AsyncMock())

        async with self.sessions() as session:
            user = await session.get(User, 901)
            with patch("bot.handlers.start._any_sponsor_provider_configured", return_value=False):
                await cmd_start(
                    message, session, user, is_new_user=False,
                    bot=SimpleNamespace(), state=state,
                    previous_last_seen_at=inactive_since,
                )

        async with self.sessions() as session:
            saved = await session.get(User, 901)

        self.assertIsNone(saved.pending_reactivation_referrer_id)
        self.assertTrue(saved.sponsors_verified)
        self.assertEqual(saved.sponsor_wave, 3)
        self.assertIsNotNone(saved.sponsor_wave_one)  # the original wave, untouched

    async def test_admin_promotion_mid_flow_clears_pending_reactivation_without_paying(self) -> None:
        """A reactivation armed while the user was still a normal referred
        user, then promoted to admin before finishing the fresh wall: the
        admin-bypass branch must not try to resolve it against the wiped
        (empty) wave -- just clear it silently, no crash, no misleading
        message, no (impossible-to-earn) payout."""
        from bot.handlers.start import cmd_start

        inactive_since, referrer, referred = await self._eligible_returning_user(
            910, 911, tos_accepted=True,
        )
        async with self.sessions() as session:
            user = await session.get(User, 911)
            self.assertTrue(await trigger_referral_reactivation_wall(user, 910, inactive_since, session))

        message = SimpleNamespace(
            text="/start ref_910", answer=AsyncMock(), answer_photo=AsyncMock(),
        )
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), clear=AsyncMock())
        bot = _bot()

        async with self.sessions() as session:
            user = await session.get(User, 911)
            user.is_admin = True  # promoted before finishing the fresh wall
            await session.commit()
            await cmd_start(
                message, session, user, is_new_user=False,
                bot=bot, state=state, previous_last_seen_at=inactive_since,
            )

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 910)
            saved_referred = await session.get(User, 911)

        self.assertIsNone(saved_referred.pending_reactivation_referrer_id)
        self.assertTrue(saved_referred.sponsors_verified)
        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
