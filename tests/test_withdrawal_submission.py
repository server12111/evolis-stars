import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User, Withdrawal
from bot.handlers.withdraw import msg_captcha


class WithdrawalSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_admin_channel_failure_does_not_take_stars(self) -> None:
        async with self.sessions() as session:
            user = User(
                user_id=500,
                username="withdraw_user",
                first_name="User",
                stars_balance=Decimal(20),
            )
            session.add(user)
            await session.commit()

            message = SimpleNamespace(text="4", answer=AsyncMock())
            state = SimpleNamespace(
                get_data=AsyncMock(
                    return_value={"amount": 15, "captcha_answer": 4}
                ),
                clear=AsyncMock(),
            )
            bot = SimpleNamespace(
                send_message=AsyncMock(side_effect=RuntimeError("offline")),
                delete_message=AsyncMock(),
            )

            with (
                patch("bot.handlers.withdraw.settings.admin_channel_id", "123"),
                patch("bot.handlers.withdraw.settings.payments_channel_id", ""),
                patch("bot.handlers.withdraw.settings.payments_channel_link", ""),
            ):
                await msg_captcha(message, user, session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 500)
            withdrawals = (
                await session.execute(select(func.count(Withdrawal.id)))
            ).scalar_one()

        self.assertEqual(float(saved_user.stars_balance), 20.0)
        self.assertEqual(withdrawals, 0)
        message.answer.assert_awaited()
