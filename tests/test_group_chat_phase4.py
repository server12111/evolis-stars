import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, ChatLinkClick, User
from bot.database.repositories.chat_ads import ChatAdRepository
from bot.database.repositories.link_clicks import LinkButtonRepository, LinkClickRepository
from bot.handlers.group.link_click import cb_link_click
from bot.handlers.group.owner_menu import cb_chat_broadcast_toggle
from bot.services.chat_revenue import settle_chat_ad_revenue


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class AdRevenueSettlementTests(ChatModelsTestCase):
    async def test_pays_half_star_per_thousand_views_exactly_once(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="Owner", stars_balance=Decimal("0")))
            chat = Chat(chat_id=-1, title="T", owner_user_id=1, status="active")
            session.add(chat)
            await session.commit()

            ad_repo = ChatAdRepository(session)
            for i in range(1000):
                await ad_repo.record_send(-1, 900000 + i)

            await settle_chat_ad_revenue(session, chat)

        async with self.sessions() as session:
            owner = await session.get(User, 1)
            chat = await session.get(Chat, -1)
        self.assertEqual(owner.stars_balance, Decimal("0.5"))
        self.assertEqual(chat.ads_revenue_paid_thresholds, 1)

        # Re-settling without new sends must not pay again.
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await settle_chat_ad_revenue(session, chat)
        async with self.sessions() as session:
            owner = await session.get(User, 1)
        self.assertEqual(owner.stars_balance, Decimal("0.5"))

    async def test_click_bonus_paid_once_at_threshold(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=2, first_name="Owner", stars_balance=Decimal("0")))
            chat = Chat(chat_id=-2, title="T", owner_user_id=2, status="active")
            session.add(chat)
            await session.commit()
            for i in range(400):
                session.add(ChatLinkClick(link_id=1, user_id=800000 + i, chat_id=-2))
            await session.commit()

            await settle_chat_ad_revenue(session, chat)

        async with self.sessions() as session:
            owner = await session.get(User, 2)
            chat = await session.get(Chat, -2)
        self.assertEqual(owner.stars_balance, Decimal("4"))
        self.assertTrue(chat.ads_bonus_paid)

        # Re-settling must not pay the bonus twice.
        async with self.sessions() as session:
            chat = await session.get(Chat, -2)
            await settle_chat_ad_revenue(session, chat)
        async with self.sessions() as session:
            owner = await session.get(User, 2)
        self.assertEqual(owner.stars_balance, Decimal("4"))

    async def test_no_owner_means_no_payout_attempt(self) -> None:
        async with self.sessions() as session:
            chat = Chat(chat_id=-3, title="T", owner_user_id=None, status="active")
            session.add(chat)
            await session.commit()
            # Should not raise despite no owner to credit.
            await settle_chat_ad_revenue(session, chat)


class LinkClickTests(ChatModelsTestCase):
    def _callback(self, chat_id: int, user_id: int, link_id: int, chat_type: str = "supergroup"):
        message = SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=chat_type))
        return SimpleNamespace(
            message=message,
            from_user=SimpleNamespace(id=user_id),
            data=f"lc:{link_id}",
            answer=AsyncMock(),
        )

    async def test_first_click_counted_repeat_click_not_double_counted(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-5, title="T", owner_user_id=1, status="active"))
            btn = await LinkButtonRepository(session).create("Ad", "https://example.com", created_by=1)

        cb1 = self._callback(-5, 500, btn.id)
        async with self.sessions() as session:
            await cb_link_click(cb1, session)
        cb1.answer.assert_awaited_once()
        self.assertTrue(cb1.answer.await_args.kwargs["url"].endswith(f"?start=lc_{btn.id}"))

        cb2 = self._callback(-5, 500, btn.id)  # same user clicks again
        async with self.sessions() as session:
            await cb_link_click(cb2, session)

        async with self.sessions() as session:
            count = await LinkClickRepository(session).count_for_chat(-5)
        self.assertEqual(count, 1)

    async def test_inactive_link_rejected(self) -> None:
        async with self.sessions() as session:
            btn = await LinkButtonRepository(session).create("Ad", "https://example.com", created_by=1)
            await LinkButtonRepository(session).delete(btn.id)

        cb = self._callback(-6, 500, btn.id)
        async with self.sessions() as session:
            await cb_link_click(cb, session)
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.args or "show_alert" in cb.answer.await_args.kwargs)


class BroadcastToggleTests(ChatModelsTestCase):
    async def test_owner_can_toggle_non_owner_cannot(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-7, title="T", owner_user_id=42, status="active", broadcast_opt_in=False))
            await session.commit()

        message = SimpleNamespace(chat=SimpleNamespace(id=-7, title="T"), edit_text=AsyncMock(), answer=AsyncMock())
        cb = SimpleNamespace(message=message, from_user=SimpleNamespace(id=42), answer=AsyncMock())
        async with self.sessions() as session:
            await cb_chat_broadcast_toggle(cb, session)
        cb.answer.assert_awaited_with("✅ Включено")

        async with self.sessions() as session:
            chat = await session.get(Chat, -7)
        self.assertTrue(chat.broadcast_opt_in)

        # A non-owner's tap does nothing.
        message2 = SimpleNamespace(chat=SimpleNamespace(id=-7, title="T"), edit_text=AsyncMock(), answer=AsyncMock())
        cb2 = SimpleNamespace(message=message2, from_user=SimpleNamespace(id=999), answer=AsyncMock())
        async with self.sessions() as session:
            await cb_chat_broadcast_toggle(cb2, session)
        cb2.answer.assert_awaited_once_with()
        async with self.sessions() as session:
            chat = await session.get(Chat, -7)
        self.assertTrue(chat.broadcast_opt_in)  # unchanged


class SendAdReturnValueTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_ad_returns_true_only_for_code_1(self) -> None:
        from bot.services import adv

        class _FakeResp:
            def __init__(self, code):
                self.status = 200
                self._code = code

            async def json(self, content_type=None):
                return {"SendPostResult": self._code}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _FakeSession:
            def __init__(self, code):
                self._code = code

            def post(self, *a, **kw):
                return _FakeResp(self._code)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        for code, expected in [(1, True), (7, False), (8, False), (5, False), (11, False)]:
            with patch("aiohttp.ClientSession", lambda *a, **kw: _FakeSession(code)):
                result = await adv.send_ad("key", 123, hi=False)
            self.assertEqual(result, expected, f"code={code}")


if __name__ == "__main__":
    unittest.main()
