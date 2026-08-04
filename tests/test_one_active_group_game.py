import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.group.games_doors import msg_doors_start
from bot.handlers.group.games_maze import msg_maze_start
from bot.handlers.group.games_roulette import msg_roulette_bet
from bot.handlers.group.games_tower import msg_tower_start
from bot.services.chat_games import place_bet


def _message(chat_id: int, user_id: int, text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Chat"),
        from_user=SimpleNamespace(id=user_id, first_name="U"),
        text=text,
        reply=AsyncMock(),
        answer=AsyncMock(),
    )


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


class OneActiveGameAcrossTypesTests(ChatModelsTestCase):
    async def test_starting_doors_blocks_a_different_game_type_tower(self) -> None:
        await self._add_user(1, "100")
        # Start Doors — leaves an active round behind.
        doors_msg = _message(-1, 1, "двери 10")
        async with self.sessions() as session:
            await msg_doors_start(doors_msg, session)

        # Tower, a DIFFERENT game type, must now be blocked.
        tower_msg = _message(-1, 1, "башня 10")
        async with self.sessions() as session:
            await msg_tower_start(tower_msg, session)

        rendered = tower_msg.reply.await_args.args[0]
        self.assertIn("уже есть активная игра", rendered)
        self.assertIn("Двери", rendered)

        async with self.sessions() as session:
            user = await session.get(User, 1)
        # Only the Doors bet was ever deducted — Tower's attempt never charged.
        self.assertEqual(user.stars_balance, Decimal("90"))

    async def test_blocks_across_different_chats_too(self) -> None:
        await self._add_user(2, "100")
        doors_msg = _message(-1, 2, "двери 10")
        async with self.sessions() as session:
            await msg_doors_start(doors_msg, session)

        maze_msg = _message(-2, 2, "лабиринт 10")  # a different chat entirely
        async with self.sessions() as session:
            await msg_maze_start(maze_msg, session)

        rendered = maze_msg.reply.await_args.args[0]
        self.assertIn("уже есть активная игра", rendered)

    async def test_roulette_is_blocked_while_another_game_is_active(self) -> None:
        await self._add_user(3, "100")
        tower_msg = _message(-1, 3, "башня 10")
        async with self.sessions() as session:
            await msg_tower_start(tower_msg, session)

        roulette_msg = _message(-1, 3, "ред 10")
        async with self.sessions() as session:
            await msg_roulette_bet(roulette_msg, session)

        rendered = roulette_msg.reply.await_args.args[0]
        self.assertIn("уже есть активная игра", rendered)

    async def test_different_users_are_not_blocked_by_each_other(self) -> None:
        await self._add_user(4, "100")
        await self._add_user(5, "100")
        tower_msg = _message(-1, 4, "башня 10")
        async with self.sessions() as session:
            await msg_tower_start(tower_msg, session)

        doors_msg = _message(-1, 5, "двери 10")
        async with self.sessions() as session:
            await msg_doors_start(doors_msg, session)

        # Doors started successfully for user 5 — no "already active" reply,
        # since user 4's active Tower round doesn't affect a different user.
        doors_msg.reply.assert_not_awaited()

    async def test_place_bet_directly_reports_active_game_label(self) -> None:
        await self._add_user(6, "100")
        tower_msg = _message(-1, 6, "башня 10")
        async with self.sessions() as session:
            await msg_tower_start(tower_msg, session)

        async with self.sessions() as session:
            ok, error, needs_registration = await place_bet(session, 6, 10.0, 1.0)
        self.assertFalse(ok)
        self.assertIn("Башня", error)
        self.assertFalse(needs_registration)


if __name__ == "__main__":
    unittest.main()
