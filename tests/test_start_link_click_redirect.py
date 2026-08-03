import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.repositories.link_clicks import LinkButtonRepository
from bot.handlers.start import cmd_start


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


def _message(text: str):
    return SimpleNamespace(text=text, answer=AsyncMock())


def _fsm_state():
    return SimpleNamespace(get_state=AsyncMock(return_value=None), clear=AsyncMock())


class StartLinkClickRedirectTests(ChatModelsTestCase):
    async def test_lc_payload_shows_real_destination_button_and_skips_onboarding(self) -> None:
        async with self.sessions() as session:
            button = await LinkButtonRepository(session).create(
                "Наш канал", "https://t.me/somechannel", created_by=1
            )

        message = _message(f"/start lc_{button.id}")
        db_user = SimpleNamespace(user_id=999, is_admin=False)
        async with self.sessions() as session:
            await cmd_start(
                message, session, db_user, is_new_user=True,
                bot=SimpleNamespace(), state=_fsm_state(),
            )

        message.answer.assert_awaited_once()
        args, kwargs = message.answer.call_args
        self.assertIn("Наш канал", args[0])
        markup = kwargs["reply_markup"]
        urls = [btn.url for row in markup.inline_keyboard for btn in row if btn.url]
        self.assertIn("https://t.me/somechannel", urls)

    async def test_unknown_lc_id_replies_nothing_but_does_not_crash(self) -> None:
        message = _message("/start lc_999999")
        db_user = SimpleNamespace(user_id=999, is_admin=False)
        async with self.sessions() as session:
            await cmd_start(
                message, session, db_user, is_new_user=True,
                bot=SimpleNamespace(), state=_fsm_state(),
            )
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
