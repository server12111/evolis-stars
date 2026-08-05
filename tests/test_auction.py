import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import AuctionRound, User
from bot.handlers.auction import cb_auction_bid


def _callback(user_id: int, data: str, bot: AsyncMock) -> SimpleNamespace:
    message = SimpleNamespace(edit_text=AsyncMock())
    return SimpleNamespace(
        message=message, data=data, bot=bot, answer=AsyncMock(),
    )


class AuctionOutbidTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="A", stars_balance=Decimal("100")))
            session.add(User(user_id=2, first_name="B", stars_balance=Decimal("100")))
            session.add(User(user_id=3, first_name="C", stars_balance=Decimal("100")))
            session.add(AuctionRound(
                id=1, status="active", current_bid=Decimal("0"), prize_pool=Decimal("0"),
                start_at=datetime.utcnow(), end_at=datetime.utcnow() + timedelta(hours=1),
            ))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _bid(self, user_id: int, amount: int, bot: AsyncMock) -> None:
        async with self.sessions() as session:
            db_user = await session.get(User, user_id)
            cb = _callback(user_id, f"auction:bid:{amount}", bot)
            await cb_auction_bid(cb, db_user, session)

    async def test_outbid_message_shows_the_bid_increment_not_the_pool(self) -> None:
        bot_a = AsyncMock()
        await self._bid(1, 5, bot_a)  # A leads with +5

        bot_b = AsyncMock()
        await self._bid(2, 10, bot_b)  # B outbids A with +10

        bot_b.send_message.assert_awaited_once()
        args, kwargs = bot_b.send_message.await_args
        self.assertEqual(args[0], 1)  # DM goes to A, the outbid user
        rendered = args[1]
        # The "new bid" figure must be the increment B just added (10),
        # not the cumulative prize pool total (15).
        self.assertIn("Новая ставка: <b>10.00 RP⭐️</b>", rendered)
        self.assertIn("Призовой фонд: <b>15.00 RP⭐️</b>", rendered)
        # Must not silently imply a large cumulative amount is required —
        # any bid of at least 1 RP⭐️ reclaims the lead.
        self.assertIn("от 1 RP⭐️", rendered)
        self.assertNotIn("Минимальная ставка для лидерства", rendered)

    async def test_only_the_immediately_preceding_leader_is_notified(self) -> None:
        bot_a = AsyncMock()
        await self._bid(1, 5, bot_a)  # A leads

        bot_b = AsyncMock()
        await self._bid(2, 10, bot_b)  # B outbids A -> A notified
        bot_b.send_message.assert_awaited_once()
        self.assertEqual(bot_b.send_message.await_args.args[0], 1)

        bot_c = AsyncMock()
        await self._bid(3, 20, bot_c)  # C outbids B -> only B notified, NOT A
        bot_c.send_message.assert_awaited_once()
        self.assertEqual(bot_c.send_message.await_args.args[0], 2)

    async def test_self_rebid_does_not_notify_self(self) -> None:
        bot_a = AsyncMock()
        await self._bid(1, 5, bot_a)  # A leads
        await self._bid(1, 3, bot_a)  # A raises their own lead again
        bot_a.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
