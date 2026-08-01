import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BotSettings, ReferralReactivation, User
from bot.services.referral import (
    check_referral_reward,
    get_tg_reward,
    get_web_reward,
    reward_returning_referral,
)


def _wave(*urls: str) -> str:
    return json.dumps([{"url": url, "provider": "tgrass", "name": "Sponsor"} for url in urls])


class ReferralReactivationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_tg_and_web_rewards_round_to_stars_precision(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="tg_sponsor_reward", value="0.503"))
            session.add(BotSettings(key="web_sponsor_reward", value="0.255"))
            await session.commit()
            tg_reward = await get_tg_reward(session)
            web_reward = await get_web_reward(session)
        self.assertEqual(tg_reward, Decimal("0.50"))
        self.assertEqual(web_reward, Decimal("0.26"))

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
                sponsor_wave_one=_wave(
                    "https://t.me/one", "https://t.me/two"
                ),
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

        # 2 TG sponsors * 0.5 default reward, halved for a reactivation.
        self.assertEqual(first, Decimal("0.50"))
        self.assertIsNone(second)
        self.assertEqual(float(saved_referrer.stars_balance), 0.5)
        self.assertEqual(ledger_count, 1)

    async def test_ordinary_referral_reward_is_paid_once_and_marks_counted(self) -> None:
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
                sponsors_verified=True,
                sponsor_wave_one=_wave(
                    "https://t.me/a",
                    "https://t.me/b",
                    "https://t.me/c",
                    "https://t.me/d",
                    "https://t.me/e",
                    "https://t.me/f",
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)
            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 710)
            saved_referred = await session.get(User, 711)

        # 6 TG sponsors * 0.5 default reward.
        self.assertEqual(float(saved_referrer.stars_balance), 3.0)
        self.assertEqual(saved_referrer.referrals_count, 1)
        self.assertTrue(saved_referred.referral_reward_given)
        self.assertTrue(saved_referred.referral_counted)

    async def test_reward_withheld_when_below_min_sponsors(self) -> None:
        async with self.sessions() as session:
            referrer = User(
                user_id=720,
                first_name="Referrer",
                stars_balance=Decimal(0),
            )
            referred = User(
                user_id=721,
                first_name="Referred",
                referrer_id=720,
                sponsors_verified=True,
                sponsor_wave_one=_wave("https://t.me/a"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 720)
            saved_referred = await session.get(User, 721)

        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertEqual(saved_referrer.referrals_count, 0)
        self.assertTrue(saved_referred.referral_reward_given)
        self.assertFalse(saved_referred.referral_counted)


if __name__ == "__main__":
    unittest.main()
