import json
import unittest
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.services.referral import check_referral_reward, reward_returning_referral


def _wave_json(items: list[dict]) -> str:
    return json.dumps(items)


def _clean(n: int, prefix: str = "tg") -> list[dict]:
    return [{"url": f"https://t.me/{prefix}{i}", "provider": "tgrass", "name": "S"} for i in range(n)]


def _fraud(n: int, prefix: str = "fraud") -> list[dict]:
    return [
        {"url": f"https://t.me/{prefix}{i}", "provider": "tgrass", "name": "S", "fraud": True}
        for i in range(n)
    ]


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class ReferralFraudHardBlockTests(DbTestCase):
    """Regression: a referral with 9 fraud-flagged tgrass items out of 26
    total still had 17 legitimate ones left -- comfortably above the 13+
    top-tier threshold -- and check_referral_reward paid the referrer the
    full top reward anyway, since it only ever excluded the fraud items
    from the COUNT rather than disqualifying the referral outright. Per
    product decision: any fraud verdict on this account, on even a single
    sponsor, must permanently block ANY reward for this referred user,
    regardless of how many other legitimate sponsors they have."""

    async def test_referral_with_fraud_flag_is_never_paid_despite_plenty_of_legit_sponsors(self) -> None:
        referrer = User(user_id=1, first_name="Referrer", stars_balance=Decimal(0))
        referred = User(
            user_id=2,
            first_name="Referred",
            referrer_id=1,
            sponsors_verified=True,
            referral_fraud_flagged=True,
            sponsor_wave_one=_wave_json(_clean(17) + _fraud(9)),
        )
        async with self.sessions() as session:
            session.add_all((referrer, referred))
            await session.commit()
            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            referrer_row = await session.get(User, 1)
            referred_row = await session.get(User, 2)

        self.assertEqual(float(referrer_row.stars_balance), 0.0)
        self.assertEqual(referrer_row.referrals_count, 0)
        self.assertFalse(referred_row.referral_reward_given)

    async def test_referral_without_fraud_flag_still_pays_normally(self) -> None:
        """Sanity check: the hard block must not accidentally suppress
        genuinely clean referrals."""
        referrer = User(user_id=1, first_name="Referrer", stars_balance=Decimal(0))
        referred = User(
            user_id=2,
            first_name="Referred",
            referrer_id=1,
            sponsors_verified=True,
            referral_fraud_flagged=False,
            sponsor_wave_one=_wave_json(_clean(17)),
        )
        async with self.sessions() as session:
            session.add_all((referrer, referred))
            await session.commit()
            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            referrer_row = await session.get(User, 1)
            referred_row = await session.get(User, 2)

        self.assertGreater(float(referrer_row.stars_balance), 0.0)
        self.assertTrue(referred_row.referral_reward_given)

    async def test_reactivation_reward_is_also_blocked_for_a_fraud_flagged_account(self) -> None:
        from datetime import datetime, timedelta

        inactive_since = datetime.utcnow() - timedelta(days=8)
        referrer = User(user_id=1, first_name="Referrer", stars_balance=Decimal(0))
        referred = User(
            user_id=2,
            first_name="Referred",
            referrer_id=1,
            referral_counted=True,
            referral_reward_given=True,
            referral_fraud_flagged=True,
            last_seen_at=inactive_since,
            sponsor_wave_one=_wave_json(_clean(17)),
        )
        async with self.sessions() as session:
            session.add_all((referrer, referred))
            await session.commit()
            reward = await reward_returning_referral(referred, 1, inactive_since, session)

        self.assertIsNone(reward)


if __name__ == "__main__":
    unittest.main()
