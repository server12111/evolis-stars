import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BotSettings, User
from bot.handlers.admin.settings import SETTING_LABELS, cb_settings
from bot.keyboards.admin.settings import NUMERIC_SETTINGS


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


REWARD_KEYS = ["referral_reward_0_3", "referral_reward_4_5", "referral_reward_premium"]


class ReferralRewardPremiumSettingTests(unittest.TestCase):
    def test_settings_are_editable_via_admin_panel(self) -> None:
        for key in REWARD_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, SETTING_LABELS)

    def test_settings_have_menu_buttons(self) -> None:
        keys = [key for key, _ in NUMERIC_SETTINGS]
        for key in REWARD_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, keys)


class AdminSettingsScreenTests(ChatModelsTestCase):
    async def test_summary_shows_reward_amounts(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward_premium", value="9"))
            await session.commit()

        db_user = User(user_id=1, first_name="Admin", is_admin=True)
        message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
        callback = SimpleNamespace(message=message, answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())

        async with self.sessions() as session:
            await cb_settings(callback, db_user, session, state)

        rendered = message.edit_text.await_args.args[0]
        self.assertIn("Premium-реферала", rendered)
        self.assertIn("9.00", rendered)


if __name__ == "__main__":
    unittest.main()
