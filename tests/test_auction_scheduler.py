import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import AuctionRound, User
from bot.services.auction_scheduler import _run_pass


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class AuctionPhantomWinnerTests(ChatModelsTestCase):
    async def test_missing_winner_row_defers_instead_of_closing_the_round(self) -> None:
        """If current_bidder_id points at a user row that no longer
        exists, the round must NOT be marked finished — otherwise the
        prize is marked paid without ever being credited to anyone."""
        async with self.sessions() as session:
            session.add(AuctionRound(
                id=1, status="active", current_bid=Decimal("50"), current_bidder_id=999,
                prize_pool=Decimal("50"),
                start_at=datetime.utcnow() - timedelta(hours=9),
                end_at=datetime.utcnow() - timedelta(hours=1),
            ))
            await session.commit()

        bot = AsyncMock()
        with patch("bot.services.auction_scheduler.SessionFactory", self.sessions):
            await _run_pass(bot)

        bot.send_message.assert_not_awaited()
        async with self.sessions() as session:
            round_ = await session.get(AuctionRound, 1)
        self.assertEqual(round_.status, "active")  # not closed out

    async def test_real_winner_gets_credited_and_round_closes(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="W", stars_balance=Decimal("0")))
            session.add(AuctionRound(
                id=2, status="active", current_bid=Decimal("50"), current_bidder_id=1,
                prize_pool=Decimal("50"),
                start_at=datetime.utcnow() - timedelta(hours=9),
                end_at=datetime.utcnow() - timedelta(hours=1),
            ))
            await session.commit()

        bot = AsyncMock()
        with patch("bot.services.auction_scheduler.SessionFactory", self.sessions):
            await _run_pass(bot)

        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.args[0], 1)
        async with self.sessions() as session:
            round_ = await session.get(AuctionRound, 2)
            winner = await session.get(User, 1)
        self.assertEqual(round_.status, "finished")
        self.assertEqual(winner.stars_balance, Decimal("40.00"))  # 50 * (1 - 0.20)


if __name__ == "__main__":
    unittest.main()
