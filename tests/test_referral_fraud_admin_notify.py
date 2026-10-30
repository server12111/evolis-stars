import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.services.premium_emoji import FAKE_BADGE_EN
from bot.services.referral import notify_fraud_sponsors, settings


def _wave_json(items: list[dict]) -> str:
    return json.dumps(items)


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class FraudAdminNotifyTests(DbTestCase):
    """notify_fraud_sponsors already tells the flagged user and their
    referrer -- this is the third leg, an internal audit copy to the
    admin channel, using the pack's English FAKE badge (the RU one is
    reserved for the user/referrer-facing notices)."""

    async def test_admin_channel_gets_notified_with_english_badge(self) -> None:
        user = User(
            user_id=1, first_name="U", username="flagged",
            sponsor_wave_one=_wave_json([
                {"url": "https://t.me/a", "provider": "tgrass", "name": "S", "fraud": True},
            ]),
        )
        async with self.sessions() as session:
            session.add(user)
            await session.commit()
            bot = AsyncMock()
            with patch.object(settings, "admin_channel_id", "-100123"):
                await notify_fraud_sponsors(user, session, bot)

        admin_call = next(
            call for call in bot.send_message.await_args_list if call.args[0] == -100123
        )
        text = admin_call.args[1]
        self.assertIn(f'tg-emoji emoji-id="{FAKE_BADGE_EN}"', text)
        self.assertIn("flagged", text)
        self.assertIn(str(user.user_id), text)

    async def test_no_admin_channel_configured_does_not_crash(self) -> None:
        user = User(
            user_id=2, first_name="U",
            sponsor_wave_one=_wave_json([
                {"url": "https://t.me/a", "provider": "tgrass", "name": "S", "fraud": True},
            ]),
        )
        async with self.sessions() as session:
            session.add(user)
            await session.commit()
            bot = AsyncMock()
            with patch.object(settings, "admin_channel_id", ""):
                await notify_fraud_sponsors(user, session, bot)

        # Only the user-facing notice fires -- no admin channel configured.
        self.assertEqual(bot.send_message.await_count, 1)


if __name__ == "__main__":
    unittest.main()
