import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat
from bot.database.repositories.link_clicks import LinkButtonRepository
from bot.services.chat_ad_scheduler import _maybe_post_click_ad, _run_pass


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class ClickAdCooldownTests(ChatModelsTestCase):
    async def _setup(self, chat_id: int, last_posted: datetime | None) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=chat_id, title="T", status="active", broadcast_opt_in=True,
                last_click_ad_posted_at=last_posted,
            ))
            await LinkButtonRepository(session).create("Click me", "https://example.com", created_by=1)
            await session.commit()

    async def test_first_post_persists_timestamp_on_the_chat_row(self) -> None:
        await self._setup(-1, last_posted=None)
        bot = AsyncMock()
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _maybe_post_click_ad(bot, session, chat)

        bot.send_message.assert_awaited_once()
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertIsNotNone(chat.last_click_ad_posted_at)

    async def test_second_call_within_cooldown_does_not_repost(self) -> None:
        await self._setup(-2, last_posted=datetime.utcnow() - timedelta(minutes=5))
        bot = AsyncMock()
        async with self.sessions() as session:
            chat = await session.get(Chat, -2)
            await _maybe_post_click_ad(bot, session, chat)
        bot.send_message.assert_not_awaited()

    async def test_restart_does_not_reset_the_cooldown(self) -> None:
        """A process restart used to forget the in-memory "last posted"
        timestamp entirely and immediately re-post — this simulates exactly
        that: a chat that was posted to 5 minutes ago, loaded fresh from the
        DB as if by a brand-new process, must still honor the cooldown."""
        await self._setup(-3, last_posted=datetime.utcnow() - timedelta(minutes=5))
        bot = AsyncMock()
        async with self.sessions() as session:
            fresh_chat = await session.get(Chat, -3)
            await _maybe_post_click_ad(bot, session, fresh_chat)
        bot.send_message.assert_not_awaited()

    async def test_cooldown_expired_allows_a_new_post(self) -> None:
        await self._setup(-4, last_posted=datetime.utcnow() - timedelta(hours=4))
        bot = AsyncMock()
        async with self.sessions() as session:
            chat = await session.get(Chat, -4)
            await _maybe_post_click_ad(bot, session, chat)
        bot.send_message.assert_awaited_once()


class RunPassNeverAutoPostsTests(ChatModelsTestCase):
    async def test_run_pass_never_posts_a_click_ad_into_a_chat(self) -> None:
        """_maybe_post_click_ad must never be reached from the scheduler's
        own loop — item 1's exact bug report (an ad button appearing in a
        chat with no owner action). A fully eligible chat (opted in, no
        recent post, an active link button available) is the strongest
        case: if this doesn't send, nothing will."""
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-99, title="T", status="active", broadcast_opt_in=True,
                last_click_ad_posted_at=None,
            ))
            await LinkButtonRepository(session).create("Click me", "https://example.com", created_by=1)
            await session.commit()

        bot = AsyncMock()
        with patch("bot.services.chat_ad_scheduler.SessionFactory", self.sessions):
            await _run_pass(bot)

        bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
