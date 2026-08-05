import unittest
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, ChatAdSend, ChatLinkClick, User
from bot.services.chat_revenue import settle_chat_ad_revenue


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _setup(self, chat_id: int, owner_id: int, sends: int, clicks: int) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=owner_id, first_name="Owner", stars_balance=Decimal("0")))
            session.add(Chat(chat_id=chat_id, title="T", status="active", owner_user_id=owner_id))
            for i in range(sends):
                session.add(ChatAdSend(chat_id=chat_id, user_id=owner_id + 1000 + i))
            for i in range(clicks):
                session.add(ChatLinkClick(link_id=1, user_id=owner_id + 2000 + i, chat_id=chat_id))
            await session.commit()


class ThresholdPayoutTests(ChatModelsTestCase):
    async def test_crossing_a_threshold_pays_once(self) -> None:
        await self._setup(-1, 1, sends=1000, clicks=0)
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await settle_chat_ad_revenue(session, chat)

        async with self.sessions() as session:
            owner = await session.get(User, 1)
            chat = await session.get(Chat, -1)
        self.assertEqual(owner.stars_balance, Decimal("0.5"))
        self.assertEqual(chat.ads_revenue_paid_thresholds, 1)

    async def test_concurrent_settle_calls_only_pay_the_threshold_once(self) -> None:
        """Two overlapping calls (e.g. the background sweep and a live
        click handler racing for the same chat) must not both credit the
        same newly-crossed threshold."""
        await self._setup(-2, 2, sends=1000, clicks=0)

        async with self.sessions() as session_a, self.sessions() as session_b:
            chat_a = await session_a.get(Chat, -2)
            chat_b = await session_b.get(Chat, -2)
            # Both read the same pre-payout state before either commits.
            await settle_chat_ad_revenue(session_a, chat_a)
            await settle_chat_ad_revenue(session_b, chat_b)

        async with self.sessions() as session:
            owner = await session.get(User, 2)
            chat = await session.get(Chat, -2)
        self.assertEqual(owner.stars_balance, Decimal("0.5"))
        self.assertEqual(chat.ads_revenue_paid_thresholds, 1)

    async def test_already_paid_threshold_is_not_repaid(self) -> None:
        await self._setup(-3, 3, sends=1000, clicks=0)
        async with self.sessions() as session:
            chat = await session.get(Chat, -3)
            await settle_chat_ad_revenue(session, chat)
        async with self.sessions() as session:
            chat = await session.get(Chat, -3)
            await settle_chat_ad_revenue(session, chat)

        async with self.sessions() as session:
            owner = await session.get(User, 3)
        self.assertEqual(owner.stars_balance, Decimal("0.5"))


class ClickBonusTests(ChatModelsTestCase):
    async def test_reaching_click_threshold_pays_bonus_once(self) -> None:
        await self._setup(-4, 4, sends=0, clicks=400)
        async with self.sessions() as session:
            chat = await session.get(Chat, -4)
            await settle_chat_ad_revenue(session, chat)

        async with self.sessions() as session:
            owner = await session.get(User, 4)
            chat = await session.get(Chat, -4)
        self.assertEqual(owner.stars_balance, Decimal("4"))
        self.assertTrue(chat.ads_bonus_paid)

    async def test_concurrent_settle_calls_only_pay_click_bonus_once(self) -> None:
        await self._setup(-5, 5, sends=0, clicks=400)

        async with self.sessions() as session_a, self.sessions() as session_b:
            chat_a = await session_a.get(Chat, -5)
            chat_b = await session_b.get(Chat, -5)
            await settle_chat_ad_revenue(session_a, chat_a)
            await settle_chat_ad_revenue(session_b, chat_b)

        async with self.sessions() as session:
            owner = await session.get(User, 5)
        self.assertEqual(owner.stars_balance, Decimal("4"))


if __name__ == "__main__":
    unittest.main()
