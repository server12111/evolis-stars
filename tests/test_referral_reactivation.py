import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BotSettings, ReferralReactivation, User
from bot.services.referral import (
    check_referral_reward,
    get_return_reward,
    reward_returning_referral,
)


class ReferralReactivationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_half_reward_rounds_to_stars_precision(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward", value="3.01"))
            await session.commit()
            reward = await get_return_reward(session)
        self.assertEqual(reward, Decimal("1.51"))

    async def test_same_return_cycle_is_rewarded_only_once(self) -> None:
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            referrer = User(
                user_id=700,
                first_name="Referrer",
                stars_balance=Decimal(0),
            )
            referred = User(
                user_id=701,
                first_name="Referred",
                referrer_id=700,
                referral_counted=True,
                referral_reward_given=True,
                last_seen_at=inactive_since,
            )
            session.add_all((referrer, referred))
            await session.commit()

            first = await reward_returning_referral(
                referred,
                referrer.user_id,
                inactive_since,
                session,
            )
            second = await reward_returning_referral(
                referred,
                referrer.user_id,
                inactive_since,
                session,
            )

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 700)
            ledger_count = (
                await session.execute(
                    select(func.count(ReferralReactivation.id))
                )
            ).scalar_one()

        self.assertEqual(first, Decimal("1.50"))
        self.assertIsNone(second)
        self.assertEqual(float(saved_referrer.stars_balance), 1.5)
        self.assertEqual(ledger_count, 1)

    async def test_ordinary_referral_reward_is_paid_once(self) -> None:
        async with self.sessions() as session:
            referrer = User(
                user_id=710,
                first_name="Referrer",
                stars_balance=Decimal(0),
            )
            referred = User(
                user_id=711,
                first_name="Referred",
                referrer_id=710,
                phone_verified=True,
                sponsors_verified=True,
                tasks_completed_count=3,
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)
            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 710)
            saved_referred = await session.get(User, 711)

        self.assertEqual(float(saved_referrer.stars_balance), 3.0)
        self.assertTrue(saved_referred.referral_reward_given)


if __name__ == "__main__":
    unittest.main()
