import unittest
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BotSettings, User
from bot.services.referral import check_referral_reward


def _wave_json(*urls: str) -> str:
    import json
    return json.dumps([{"url": url, "provider": "tgrass", "name": "Sponsor"} for url in urls])


class ReferralDoublePaymentRaceTests(unittest.IsolatedAsyncioTestCase):
    """Regression coverage for a double-tap on "Я подписался на все каналы"
    (or any two near-simultaneous updates for the same referred user):
    each gets its own session and its own loaded copy of the referred
    User row. Without an atomic claim on referral_reward_given, both
    copies see referral_reward_given=False in memory and both would pay
    the referrer -- see check_referral_reward's own comment."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_second_stale_call_does_not_double_pay(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward", value="4"))
            referrer = User(user_id=950, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=951,
                first_name="Referred",
                referrer_id=950,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c"),
            )
            session.add_all((referrer, referred))
            await session.commit()

        session_a = self.sessions()
        session_b = self.sessions()
        try:
            referred_a = await session_a.get(User, 951)
            referred_b = await session_b.get(User, 951)
            # Both loaded before either call runs -- neither sees the
            # other's write, exactly like two concurrent handler
            # invocations racing on the same referred user.
            self.assertFalse(referred_a.referral_reward_given)
            self.assertFalse(referred_b.referral_reward_given)

            await check_referral_reward(referred_a, session_a)
            await check_referral_reward(referred_b, session_b)
        finally:
            await session_a.close()
            await session_b.close()

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 950)
            saved_referred = await session.get(User, 951)

        self.assertEqual(float(saved_referrer.stars_balance), 4.0)
        self.assertTrue(saved_referred.referral_reward_given)
        self.assertTrue(saved_referred.referral_counted)

    async def test_second_call_after_genuine_success_is_a_clean_no_op(self) -> None:
        """Sanity check distinct from the race above: a THIRD call using a
        FRESH (non-stale) copy loaded after the first payment must also
        stay a no-op via the normal in-memory guard."""
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward", value="4"))
            referrer = User(user_id=960, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=961,
                first_name="Referred",
                referrer_id=960,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            fresh_referred = await session.get(User, 961)
            await check_referral_reward(fresh_referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 960)
        self.assertEqual(float(saved_referrer.stars_balance), 4.0)


if __name__ == "__main__":
    unittest.main()
