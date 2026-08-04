import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.start import _proceed_after_tos


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class SponsorNotifyGuardTests(ChatModelsTestCase):
    async def test_first_verification_notifies_and_rewards_once(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U", sponsors_verified=False))
            await session.commit()

        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        with (
            patch("bot.handlers.start._send_main_menu", AsyncMock()),
            patch("bot.handlers.start.settings", SimpleNamespace(tgrass_code=None, botohub_key=None)),
            patch("bot.handlers.start.notify_user_sponsors_verified", AsyncMock()) as notify,
            patch("bot.handlers.start.check_referral_reward", AsyncMock()) as reward,
        ):
            async with self.sessions() as session:
                db_user = await session.get(User, 1)
                await _proceed_after_tos(message, db_user, session, bot=SimpleNamespace())

        notify.assert_awaited_once()
        reward.assert_awaited_once()
        async with self.sessions() as session:
            user = await session.get(User, 1)
        self.assertTrue(user.sponsors_verified)

    async def test_already_verified_user_is_not_renotified(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=2, first_name="U", sponsors_verified=True))
            await session.commit()

        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
        with (
            patch("bot.handlers.start._send_main_menu", AsyncMock()),
            patch("bot.handlers.start.notify_user_sponsors_verified", AsyncMock()) as notify,
            patch("bot.handlers.start.check_referral_reward", AsyncMock()) as reward,
        ):
            async with self.sessions() as session:
                db_user = await session.get(User, 2)
                await _proceed_after_tos(message, db_user, session, bot=SimpleNamespace())

        notify.assert_not_awaited()
        reward.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
