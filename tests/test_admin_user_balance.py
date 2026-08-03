import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.admin.users import msg_add_stars, msg_sub_stars


def _admin_user() -> User:
    return User(user_id=999, first_name="Admin", is_admin=True, stars_balance=Decimal("0"))


def _message(text: str):
    return SimpleNamespace(text=text, answer=AsyncMock())


def _state(target_id: int):
    return SimpleNamespace(
        get_data=AsyncMock(return_value={"target_id": target_id}),
        clear=AsyncMock(),
    )


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class AdminBalanceCreditTests(ChatModelsTestCase):
    async def test_add_stars_actually_credits_balance(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U", stars_balance=Decimal("10")))
            await session.commit()

        message = _message("5")
        state = _state(1)
        async with self.sessions() as session:
            # Loading the target row first (as the real callback flow does)
            # is what previously crashed the update — regression guard.
            await msg_add_stars(message, state, session, _admin_user())

        message.answer.assert_awaited_once()
        rendered = message.answer.await_args.args[0]
        self.assertIn("15.00", rendered)

        async with self.sessions() as session:
            user = await session.get(User, 1)
        self.assertEqual(user.stars_balance, Decimal("15"))

    async def test_sub_stars_actually_debits_balance(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=2, first_name="U", stars_balance=Decimal("10")))
            await session.commit()

        message = _message("4")
        state = _state(2)
        async with self.sessions() as session:
            await msg_sub_stars(message, state, session, _admin_user())

        rendered = message.answer.await_args.args[0]
        self.assertIn("6.00", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 2)
        self.assertEqual(user.stars_balance, Decimal("6"))

    async def test_sub_stars_floors_at_zero_when_insufficient(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=3, first_name="U", stars_balance=Decimal("2")))
            await session.commit()

        message = _message("10")
        state = _state(3)
        async with self.sessions() as session:
            await msg_sub_stars(message, state, session, _admin_user())

        async with self.sessions() as session:
            user = await session.get(User, 3)
        self.assertEqual(user.stars_balance, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
