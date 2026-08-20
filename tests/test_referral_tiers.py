import unittest
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.services.referral import (
    check_referral_reward,
    get_milestone_bonus,
    referral_tier_badge,
    referral_tier_for_count,
    vip_and_tier_badge,
)


def _wave_json(*urls: str) -> str:
    import json
    return json.dumps([{"url": url, "provider": "tgrass", "name": "Sponsor"} for url in urls])


class MilestoneBonusTests(unittest.IsolatedAsyncioTestCase):
    """get_milestone_bonus with no BotSettings rows -- falls back to the
    DEFAULT_SETTINGS values, which are exactly the confirmed table."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_one_time_milestones_below_200_pay_the_confirmed_amounts(self) -> None:
        """Below 200 no recurring tier is active yet, so these are pure
        one-time milestone amounts with nothing added on top."""
        expected = {
            10: Decimal("0.1"),
            20: Decimal("0.2"),
            30: Decimal("0.3"),
            40: Decimal("0.4"),
            50: Decimal("0.5"),
            65: Decimal("1"),
            80: Decimal("1.1"),
            90: Decimal("1.2"),
            100: Decimal("1.5"),
            120: Decimal("1.7"),
            140: Decimal("1.9"),
            145: Decimal("1.5"),
            150: Decimal("2"),
            155: Decimal("2.2"),
            170: Decimal("2.2"),
            190: Decimal("2.4"),
        }
        async with self.sessions() as session:
            for count, amount in expected.items():
                with self.subTest(count=count):
                    self.assertEqual(await get_milestone_bonus(session, count), amount)

    async def test_milestones_at_or_above_200_also_include_the_recurring_rate(self) -> None:
        """250/350/450 are one-time milestones that also sit above the
        200/300/400 recurring floor, so both amounts add together."""
        expected = {
            250: Decimal("3") + Decimal("1"),      # milestone + 200-tier rate
            350: Decimal("5") + Decimal("1.67"),    # milestone + 300-tier rate
            450: Decimal("5") + Decimal("2.5"),     # milestone + 400-tier rate
        }
        async with self.sessions() as session:
            for count, amount in expected.items():
                with self.subTest(count=count):
                    self.assertEqual(await get_milestone_bonus(session, count), amount)

    async def test_off_milestone_counts_below_200_pay_nothing(self) -> None:
        async with self.sessions() as session:
            for count in (1, 11, 51, 91, 101, 199):
                with self.subTest(count=count):
                    self.assertEqual(await get_milestone_bonus(session, count), Decimal("0"))

    async def test_old_infinite_100_plus_bonus_no_longer_applies(self) -> None:
        """The removed RECURRING_MILESTONE mechanic used to pay 3 RP for
        EVERY referral from 100 onward forever -- referral #110 (no longer
        a milestone, below the new 200+ recurring floor) must pay 0."""
        async with self.sessions() as session:
            self.assertEqual(await get_milestone_bonus(session, 110), Decimal("0"))

    async def test_200th_referral_pays_milestone_plus_recurring_additively(self) -> None:
        """Reaching exactly 200 unlocks the 200+ recurring rate AND hits
        the 200 one-time milestone on the very same referral."""
        async with self.sessions() as session:
            bonus = await get_milestone_bonus(session, 200)
        self.assertEqual(bonus, Decimal("3") + Decimal("1"))

    async def test_recurring_rate_replaces_not_stacks_across_tiers(self) -> None:
        async with self.sessions() as session:
            self.assertEqual(await get_milestone_bonus(session, 210), Decimal("1"))
            self.assertEqual(await get_milestone_bonus(session, 300), Decimal("1.67"))
            self.assertEqual(await get_milestone_bonus(session, 310), Decimal("1.67"))
            self.assertEqual(await get_milestone_bonus(session, 400), Decimal("2.5"))
            self.assertEqual(await get_milestone_bonus(session, 410), Decimal("2.5"))
            self.assertEqual(await get_milestone_bonus(session, 500), Decimal("3"))
            self.assertEqual(await get_milestone_bonus(session, 501), Decimal("3"))


class ReferralTierHelperTests(unittest.TestCase):
    def test_referral_tier_for_count(self) -> None:
        self.assertIsNone(referral_tier_for_count(0))
        self.assertIsNone(referral_tier_for_count(199))
        self.assertEqual(referral_tier_for_count(200), "premium")
        self.assertEqual(referral_tier_for_count(299), "premium")
        self.assertEqual(referral_tier_for_count(300), "sigma")
        self.assertEqual(referral_tier_for_count(499), "sigma")
        self.assertEqual(referral_tier_for_count(500), "good")
        self.assertEqual(referral_tier_for_count(10_000), "good")

    def test_referral_tier_badge(self) -> None:
        self.assertEqual(referral_tier_badge(None), "")
        self.assertEqual(referral_tier_badge("unknown"), "")
        self.assertIn("Premium", referral_tier_badge("premium"))
        self.assertIn("Sigma", referral_tier_badge("sigma"))
        self.assertIn("Good", referral_tier_badge("good"))

    def test_vip_and_tier_badge_shows_only_the_highest_title(self) -> None:
        """A user who is VIP and has also reached a Premium/Sigma/Good tier
        must show only the higher tier badge -- never both stacked."""
        self.assertEqual(vip_and_tier_badge(False, None), "")
        self.assertIn("VIP", vip_and_tier_badge(True, None))
        for tier in ("premium", "sigma", "good"):
            with self.subTest(tier=tier):
                # is_vip=True (always true in practice once a referrer has
                # any of these tiers, since their thresholds are all above
                # VIP's own 50) must not add "VIP" alongside the tier badge.
                badge = vip_and_tier_badge(True, tier)
                self.assertNotIn("VIP", badge)
                self.assertEqual(badge, referral_tier_badge(tier))
                # Also correct when is_vip somehow wasn't set yet.
                self.assertEqual(vip_and_tier_badge(False, tier), referral_tier_badge(tier))


class ReferralTierPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """check_referral_reward must persist referral_tier in the same atomic
    UPDATE as is_vip -- mirrors test_referral_double_payment_race.py's
    setup shape."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _pay_one_referral(self, referrer_id: int, referred_id: int) -> None:
        # 4 sponsors, not 3 -- the 0-3 tier pays 0 RP⭐️ by design (see
        # _REFERRAL_REWARD_TIERS), so 3 would be treated as insufficient
        # and never actually reach/count the referral at all.
        referred = User(
            user_id=referred_id,
            first_name="Referred",
            referrer_id=referrer_id,
            sponsors_verified=True,
            sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c", "https://t.me/d"),
        )
        async with self.sessions() as session:
            session.add(referred)
            await session.commit()
            await check_referral_reward(referred, session)

    async def test_referral_tier_is_none_below_200(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=100, first_name="Referrer", referrals_count=5, stars_balance=Decimal(0)))
            await session.commit()

        await self._pay_one_referral(100, 101)

        async with self.sessions() as session:
            referrer = await session.get(User, 100)
        self.assertIsNone(referrer.referral_tier)
        self.assertEqual(referrer.referrals_count, 6)

    async def test_crossing_200_sets_premium_tier(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=200, first_name="Referrer", referrals_count=199, stars_balance=Decimal(0)))
            await session.commit()

        await self._pay_one_referral(200, 201)

        async with self.sessions() as session:
            referrer = await session.get(User, 200)
        self.assertEqual(referrer.referral_tier, "premium")
        self.assertEqual(referrer.referrals_count, 200)

    async def test_crossing_500_sets_good_tier_and_stays_vip(self) -> None:
        async with self.sessions() as session:
            session.add(User(
                user_id=500, first_name="Referrer", referrals_count=499,
                stars_balance=Decimal(0), is_vip=True, referral_tier="sigma",
            ))
            await session.commit()

        await self._pay_one_referral(500, 501)

        async with self.sessions() as session:
            referrer = await session.get(User, 500)
        self.assertEqual(referrer.referral_tier, "good")
        self.assertTrue(referrer.is_vip)


if __name__ == "__main__":
    unittest.main()
