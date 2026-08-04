import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BotSettings, User
from bot.database.repositories.user import UserRepository
from bot.services.referral import (
    check_referral_reward,
    get_referral_reward,
    reward_returning_referral,
)


def _wave_json(*urls: str) -> str:
    import json
    return json.dumps([{"url": url, "provider": "tgrass", "name": "Sponsor"} for url in urls])


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class PremiumReferralRewardTests(ChatModelsTestCase):
    async def test_get_referral_reward_reads_premium_setting_when_flagged(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward", value="4"))
            session.add(BotSettings(key="referral_reward_premium", value="9"))
            await session.commit()
            normal = await get_referral_reward(session)
            premium = await get_referral_reward(session, is_premium=True)
        self.assertEqual(normal, Decimal("4"))
        self.assertEqual(premium, Decimal("9"))

    async def test_get_referral_reward_premium_falls_back_to_default(self) -> None:
        async with self.sessions() as session:
            premium = await get_referral_reward(session, is_premium=True)
        self.assertEqual(premium, Decimal("6"))

    async def test_premium_referred_user_pays_the_premium_rate(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward", value="4"))
            session.add(BotSettings(key="referral_reward_premium", value="9"))
            referrer = User(user_id=900, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=901,
                first_name="Referred",
                referrer_id=900,
                sponsors_verified=True,
                is_premium=True,
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 900)
            saved_referred = await session.get(User, 901)

        self.assertEqual(float(saved_referrer.stars_balance), 9.0)
        self.assertTrue(saved_referred.referral_reward_given)

    async def test_non_premium_referred_user_pays_the_normal_rate(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward", value="4"))
            session.add(BotSettings(key="referral_reward_premium", value="9"))
            referrer = User(user_id=910, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=911,
                first_name="Referred",
                referrer_id=910,
                sponsors_verified=True,
                is_premium=False,
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 910)

        self.assertEqual(float(saved_referrer.stars_balance), 4.0)

    async def test_payout_message_flags_premium_referral(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward_premium", value="9"))
            referrer = User(user_id=920, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=921,
                first_name="Referred",
                referrer_id=920,
                sponsors_verified=True,
                is_premium=True,
                sponsor_wave_one=_wave_json("https://t.me/a", "https://t.me/b", "https://t.me/c"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = SimpleNamespace(send_message=AsyncMock())
            await check_referral_reward(referred, session, bot)

        bot.send_message.assert_awaited_once()
        sent_text = bot.send_message.await_args.args[1]
        self.assertIn("💎", sent_text)

    async def test_returning_premium_referral_pays_half_the_premium_rate(self) -> None:
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward_premium", value="9"))
            referrer = User(user_id=930, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=931,
                first_name="Referred",
                referrer_id=930,
                referral_counted=True,
                referral_reward_given=True,
                is_premium=True,
                last_seen_at=inactive_since,
            )
            session.add_all((referrer, referred))
            await session.commit()

            reward = await reward_returning_referral(
                referred, referrer.user_id, inactive_since, session,
            )

        # Half of the premium rate (9 -> 4.50), not the normal rate's half.
        self.assertEqual(reward, Decimal("4.50"))


class UserRepositoryPremiumPersistenceTests(ChatModelsTestCase):
    async def test_is_premium_is_persisted_on_create(self) -> None:
        async with self.sessions() as session:
            repo = UserRepository(session)
            user, is_new, _ = await repo.get_or_create(
                user_id=1, username="u", first_name="U", is_premium=True,
            )
        self.assertTrue(is_new)
        self.assertTrue(user.is_premium)

    async def test_is_premium_is_synced_on_every_update(self) -> None:
        async with self.sessions() as session:
            repo = UserRepository(session)
            await repo.get_or_create(user_id=2, username="u", first_name="U", is_premium=True)
            user, is_new, _ = await repo.get_or_create(
                user_id=2, username="u", first_name="U", is_premium=False,
            )
        self.assertFalse(is_new)
        self.assertFalse(user.is_premium)


if __name__ == "__main__":
    unittest.main()
