import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import ChatGameRound, User
from bot.database.repositories.chat_game import ChatGameRoundRepository
from bot.services.chat_game_timeout import sweep_stale_rounds


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _add_user(self, user_id: int, balance: str) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=user_id, first_name="U", stars_balance=Decimal(balance)))
            await session.commit()

    async def _add_stale_round(self, chat_id, user_id, game_type, bet, level, state) -> None:
        stale_time = datetime.utcnow() - timedelta(minutes=5)
        async with self.sessions() as session:
            session.add(ChatGameRound(
                chat_id=chat_id, user_id=user_id, game_type=game_type,
                bet=Decimal(str(bet)), level=level, state_json=json.dumps(state),
                started_at=stale_time, updated_at=stale_time,
            ))
            await session.commit()


class TimeoutSweepTests(ChatModelsTestCase):
    # NOTE: in the real flow, place_bet() already debits the stake from the
    # user's balance the moment a round starts. These tests bypass that (they
    # insert ChatGameRound rows directly) so each starting balance below is
    # pre-debited by hand (started with 50, bet 10 -> seeded at 40) to match
    # what a real stuck round's balance would actually look like.
    async def test_no_progress_round_is_refunded_in_full(self) -> None:
        await self._add_user(1, "40")
        await self._add_stale_round(-1, 1, "doors", 10, 0, {"safe_positions": [0, 1]})

        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            await sweep_stale_rounds(bot, session)

        async with self.sessions() as session:
            user = await session.get(User, 1)
            round_ = await ChatGameRoundRepository(session).get_active(-1, 1, "doors")
        self.assertEqual(user.stars_balance, Decimal("50"))  # 40 + refunded 10
        self.assertIsNone(round_)
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.args[1]
        self.assertIn("Двери", text)
        self.assertIn("возвращена", text)

    async def test_doors_progress_auto_cashed_out(self) -> None:
        await self._add_user(2, "40")
        # Level 1 passed -> door_coeff_1 default 1.6
        await self._add_stale_round(-2, 2, "doors", 10, 1, {"safe_positions": [0, 1]})

        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            await sweep_stale_rounds(bot, session)

        async with self.sessions() as session:
            user = await session.get(User, 2)
        self.assertEqual(user.stars_balance, Decimal("40.00") + Decimal("16.00"))  # 10*1.6

    async def test_maze_progress_auto_cashed_out(self) -> None:
        await self._add_user(3, "40")
        await self._add_stale_round(-3, 3, "maze", 10, 1, {"shields": 0, "bonus": 0.0})

        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            await sweep_stale_rounds(bot, session)

        async with self.sessions() as session:
            user = await session.get(User, 3)
        # step=1, house_edge=0.1 default -> base = ((1-0.1)/0.82)**1
        expected_payout = round(10 * ((1 - 0.1) / 0.82) ** 1, 2)
        self.assertEqual(user.stars_balance, Decimal("40") + Decimal(str(expected_payout)))

    async def test_tower_progress_auto_cashed_out(self) -> None:
        await self._add_user(4, "40")
        await self._add_stale_round(-4, 4, "tower", 10, 1, {"mines": [0] * 8, "history": [1]})

        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            await sweep_stale_rounds(bot, session)

        async with self.sessions() as session:
            user = await session.get(User, 4)
        # level-1=0 -> chat_tower_coeff_0 default 1.00 (push)
        self.assertEqual(user.stars_balance, Decimal("40.00") + Decimal("10.00"))

    async def test_fresh_round_is_not_swept(self) -> None:
        await self._add_user(5, "50")
        async with self.sessions() as session:
            session.add(ChatGameRound(
                chat_id=-5, user_id=5, game_type="doors", bet=Decimal("10"),
                level=0, state_json=json.dumps({"safe_positions": [0, 1]}),
            ))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            await sweep_stale_rounds(bot, session)

        async with self.sessions() as session:
            round_ = await ChatGameRoundRepository(session).get_active(-5, 5, "doors")
        self.assertIsNotNone(round_)
        bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
