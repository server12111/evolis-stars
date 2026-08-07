import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat as TgChat
from aiogram.types import Message
from aiogram.types import SuccessfulPayment
from aiogram.types import Update
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.engine import Base
from bot.database.models import User
from bot.database.repositories.virus import VirusInfectionRepository
from bot.middlewares.virus_interference import VirusInterferenceMiddleware

settings = get_settings()


def _update(user_id: int, successful_payment: SuccessfulPayment | None = None) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.utcnow(),
            chat=TgChat(id=user_id, type="private"),
            from_user=TgUser(id=user_id, is_bot=False, first_name="A"),
            successful_payment=successful_payment,
        ),
    )


class VirusInterferenceMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.middleware = VirusInterferenceMiddleware()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _infect(self, infected_id: int, infector_id: int) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=infected_id, first_name="A", stars_balance=Decimal("0")))
            session.add(User(user_id=infector_id, first_name="B", stars_balance=Decimal("0")))
            await session.commit()
            await VirusInfectionRepository(session).create(infected_id, infector_id, "light")

    async def _run(self, update: Update, user_id: int):
        handler = AsyncMock(return_value="handled")
        async with self.sessions() as session:
            data = {"session": session, "event_from_user": SimpleNamespace(id=user_id)}
            result = await self.middleware(handler, update, data)
        return handler, result

    async def test_uninfected_user_passes_through(self) -> None:
        handler, result = await self._run(_update(1), 1)
        handler.assert_awaited_once()
        self.assertEqual(result, "handled")

    async def test_admin_always_passes_through_even_if_infected(self) -> None:
        await self._infect(1, 2)
        with (
            patch.object(settings, "admin_ids", "1"),
            patch("bot.middlewares.virus_interference.roll_interference", return_value=True),
        ):
            handler, _ = await self._run(_update(1), 1)
        handler.assert_awaited_once()

    async def test_successful_payment_always_passes_through(self) -> None:
        await self._infect(1, 2)
        payment = SuccessfulPayment(
            currency="XTR", total_amount=3, invoice_payload="x",
            telegram_payment_charge_id="c", provider_payment_charge_id="",
        )
        with patch("bot.middlewares.virus_interference.roll_interference", return_value=True):
            handler, _ = await self._run(_update(1, successful_payment=payment), 1)
        handler.assert_awaited_once()

    async def test_infected_user_blocked_on_roll_hit(self) -> None:
        await self._infect(1, 2)
        with (
            patch.object(Message, "reply", AsyncMock()) as reply,
            patch("bot.middlewares.virus_interference.roll_interference", return_value=True),
        ):
            handler, result = await self._run(_update(1), 1)
            reply.assert_awaited_once()
        handler.assert_not_awaited()
        self.assertIsNone(result)

    async def test_infected_user_passes_through_on_roll_miss(self) -> None:
        await self._infect(1, 2)
        with patch("bot.middlewares.virus_interference.roll_interference", return_value=False):
            handler, _ = await self._run(_update(1), 1)
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
