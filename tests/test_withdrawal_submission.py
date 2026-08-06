import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User, Withdrawal
from bot.handlers.withdraw import msg_captcha
from bot.services.chat_eligibility import credit_stars


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
                stars_balance=Decimal(50),  # amount=15 costs 45 RP⭐️ — must be enough to reach the admin-channel send
            )
            session.add(user)
            await session.commit()

            message = SimpleNamespace(text="4", answer=AsyncMock())
            state = SimpleNamespace(
                get_data=AsyncMock(
                    return_value={"amount": 15, "captcha_answer": 4, "recipient_username": "withdraw_user"}
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

        self.assertEqual(float(saved_user.stars_balance), 50.0)  # debited then refunded, net unchanged
        self.assertEqual(withdrawals, 0)
        message.answer.assert_awaited()
        bot.send_message.assert_awaited()  # confirms this actually reached (and failed at) the admin-channel send

    async def test_concurrent_credit_during_channel_posts_is_not_lost(self) -> None:
        """Regression: the debit used to be a Python-side
        `db_user.stars_balance = ...` held until the final commit, which
        happens after two Telegram API calls. A concurrent credit landing
        in a different session during that window (e.g. a referral
        reward) used to get silently overwritten by that final commit."""
        async with self.sessions() as session:
            user = User(
                user_id=501, username="raceuser", first_name="User", stars_balance=Decimal(50),
            )
            session.add(user)
            await session.commit()

            message = SimpleNamespace(text="4", answer=AsyncMock())
            state = SimpleNamespace(
                get_data=AsyncMock(
                    return_value={"amount": 15, "captcha_answer": 4, "recipient_username": "raceuser"}
                ),
                clear=AsyncMock(),
            )

            async def concurrent_credit(*args, **kwargs):
                # A referral reward (or any other credit) landing in a
                # SEPARATE session for the SAME user, right in the window
                # between this handler's debit and its final commit.
                async with self.sessions() as other_session:
                    await credit_stars(other_session, 501, Decimal("9"))
                return SimpleNamespace(message_id=1)

            bot = SimpleNamespace(
                send_message=AsyncMock(side_effect=concurrent_credit),
                delete_message=AsyncMock(),
            )

            with (
                patch("bot.handlers.withdraw.settings.admin_channel_id", "123"),
                patch("bot.handlers.withdraw.settings.payments_channel_id", ""),
                patch("bot.handlers.withdraw.settings.payments_channel_link", ""),
            ):
                await msg_captcha(message, user, session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 501)

        # 50 - 45 (withdrawal) + 9 (concurrent credit) = 14. Before the
        # fix this would have been 5 — the concurrent +9 clobbered.
        self.assertEqual(float(saved_user.stars_balance), 14.0)
