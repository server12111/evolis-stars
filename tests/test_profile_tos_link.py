import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.handlers.profile import cb_profile_tos


class ProfileTosLinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_shows_tos_text_with_back_button(self) -> None:
        message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
        callback = SimpleNamespace(message=message, answer=AsyncMock())

        async with self.sessions() as session:
            await cb_profile_tos(callback, session)

        message.edit_text.assert_awaited_once()
        rendered = message.edit_text.await_args.args[0]
        self.assertIn("соглашение", rendered.lower())
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        # Two document link buttons, then the back button last.
        self.assertEqual(markup.inline_keyboard[-1][0].callback_data, "menu:profile")
        doc_urls = [row[0].url for row in markup.inline_keyboard[:-1]]
        self.assertTrue(all(url and url.startswith("https://telegra.ph/") for url in doc_urls))
        self.assertEqual(len(doc_urls), 2)


if __name__ == "__main__":
    unittest.main()
