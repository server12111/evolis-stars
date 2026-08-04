import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat as TgChat, Message as TgMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.start import cb_tos_accept, cmd_start
from bot.middlewares.tos_gate import TosGateMiddleware
from bot.services.referral import notify_user_sponsors_verified
from bot.services.sponsor_waves import total_sponsor_count


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


def _user(**kwargs) -> User:
    defaults = dict(user_id=1, first_name="U", stars_balance=Decimal("0"))
    defaults.update(kwargs)
    return User(**defaults)


class SponsorCountMessageTests(unittest.TestCase):
    def test_total_sponsor_count_sums_both_waves(self) -> None:
        user = _user(
            sponsor_wave_one='[{"provider":"tgrass","url":"a"},{"provider":"tgrass","url":"b"}]',
            sponsor_wave_two='[{"provider":"botohub","url":"c"}]',
        )
        self.assertEqual(total_sponsor_count(user), 3)


class NotifySponsorsVerifiedTests(ChatModelsTestCase):
    async def test_message_includes_actual_sponsor_count(self) -> None:
        user = _user(
            user_id=10,
            referrer_id=999,
            referral_reward_given=False,
            sponsor_wave_one='[{"provider":"tgrass","url":"a"},{"provider":"tgrass","url":"b"}]',
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            await notify_user_sponsors_verified(user, session, bot)
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.args[1]
        self.assertIn("2 спонсора", text)  # Russian genitive plural for count=2

    async def test_non_referral_user_gets_no_message(self) -> None:
        user = _user(user_id=11, referrer_id=None)
        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            await notify_user_sponsors_verified(user, session, bot)
        bot.send_message.assert_not_awaited()


class TosGateMiddlewareTests(ChatModelsTestCase):
    def _message(self, chat_type: str = "private", text: str = "hello"):
        return TgMessage(
            message_id=1,
            date=datetime.utcnow(),
            chat=TgChat(id=1, type=chat_type),
            text=text,
        )

    async def test_intercepts_private_chat_when_tos_not_accepted(self) -> None:
        async with self.sessions() as session:
            db_user = _user(user_id=20, sponsors_verified=True, tos_accepted=False)
            message = self._message()
            handler = AsyncMock()
            middleware = TosGateMiddleware()
            with patch.object(TgMessage, "answer", AsyncMock()) as mock_answer:
                result = await middleware(handler, message, {"db_user": db_user, "session": session})

        handler.assert_not_awaited()
        mock_answer.assert_awaited_once()
        self.assertIsNone(result)

    async def test_gates_before_sponsor_wall_too(self) -> None:
        """ToS is now the FIRST gate — a brand new user with neither
        sponsors_verified nor tos_accepted must still see the ToS screen,
        not the sponsor wall."""
        async with self.sessions() as session:
            db_user = _user(user_id=25, sponsors_verified=False, tos_accepted=False)
            message = self._message()
            handler = AsyncMock()
            middleware = TosGateMiddleware()
            with patch.object(TgMessage, "answer", AsyncMock()) as mock_answer:
                result = await middleware(handler, message, {"db_user": db_user, "session": session})

        handler.assert_not_awaited()
        mock_answer.assert_awaited_once()
        self.assertIsNone(result)

    async def test_group_chat_never_gated(self) -> None:
        async with self.sessions() as session:
            db_user = _user(user_id=21, sponsors_verified=True, tos_accepted=False)
            message = self._message(chat_type="supergroup")
            handler = AsyncMock(return_value="ok")
            middleware = TosGateMiddleware()
            result = await middleware(handler, message, {"db_user": db_user, "session": session})

        handler.assert_awaited_once()
        self.assertEqual(result, "ok")

    async def test_already_accepted_passes_through(self) -> None:
        async with self.sessions() as session:
            db_user = _user(user_id=22, sponsors_verified=True, tos_accepted=True)
            message = self._message()
            handler = AsyncMock(return_value="ok")
            middleware = TosGateMiddleware()
            result = await middleware(handler, message, {"db_user": db_user, "session": session})

        handler.assert_awaited_once()
        self.assertEqual(result, "ok")

    async def test_start_command_bypasses_gate(self) -> None:
        async with self.sessions() as session:
            db_user = _user(user_id=23, sponsors_verified=True, tos_accepted=False)
            message = self._message(text="/start")
            handler = AsyncMock(return_value="ok")
            middleware = TosGateMiddleware()
            result = await middleware(handler, message, {"db_user": db_user, "session": session})

        handler.assert_awaited_once()
        self.assertEqual(result, "ok")


class CmdStartNeverReshowsTosTests(ChatModelsTestCase):
    async def test_already_accepted_user_skips_tos_gate_on_every_start(self) -> None:
        async with self.sessions() as session:
            db_user = _user(user_id=50, tos_accepted=True, sponsors_verified=True)
            session.add(db_user)
            await session.commit()

            message = SimpleNamespace(text="/start", answer=AsyncMock(), answer_photo=AsyncMock())
            state = SimpleNamespace(get_state=AsyncMock(return_value=None), clear=AsyncMock())
            with patch("bot.handlers.start._send_tos_gate", AsyncMock()) as gate:
                for _ in range(3):  # repeated /start calls must never re-show it
                    await cmd_start(
                        message, session, db_user, is_new_user=False,
                        bot=SimpleNamespace(), state=state,
                    )

        gate.assert_not_awaited()
        self.assertTrue(db_user.tos_accepted)


class TosAcceptCallbackTests(ChatModelsTestCase):
    async def test_accept_sets_flag_then_runs_sponsor_wall_then_main_menu(self) -> None:
        message = SimpleNamespace(delete=AsyncMock(), answer=AsyncMock(), answer_photo=AsyncMock())
        callback = SimpleNamespace(message=message, answer=AsyncMock())
        db_user = _user(user_id=30, tos_accepted=False, sponsors_verified=False)
        bot = SimpleNamespace()

        async with self.sessions() as session:
            session.add(db_user)
            await session.commit()
            with patch("bot.handlers.start.run_sponsor_wall_check", AsyncMock(return_value=True)) as mock_wall:
                await cb_tos_accept(callback, db_user, session, bot)

        self.assertTrue(db_user.tos_accepted)
        mock_wall.assert_awaited_once()  # ToS acceptance must trigger the sponsor wall next
        message.answer.assert_awaited()


if __name__ == "__main__":
    unittest.main()
