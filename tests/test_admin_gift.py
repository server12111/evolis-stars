import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.admin.gift import (
    cb_admin_gift,
    cb_gift_cancel,
    cb_gift_pick,
    cb_gift_send,
    msg_gift_description,
    msg_gift_target,
)
from bot.states.admin import AdminGiftStates


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _admin() -> User:
    return User(user_id=999, first_name="Admin", is_admin=True, stars_balance=Decimal("0"))


def _gift(gift_id: str = "gift1", star_count: int = 15, emoji: str = "🎂"):
    return SimpleNamespace(id=gift_id, star_count=star_count, sticker=SimpleNamespace(emoji=emoji))


def _callback(data: str | None = None, bot=None):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, answer=AsyncMock(), data=data, bot=bot)


def _message(text: str = ""):
    return SimpleNamespace(text=text, answer=AsyncMock())


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class GiftListTests(DbTestCase):
    async def test_non_admin_denied(self) -> None:
        cb = _callback()
        state = _state()
        non_admin = User(user_id=1, first_name="U", is_admin=False)
        await cb_admin_gift(cb, non_admin, state, SimpleNamespace())
        cb.answer.assert_awaited_once()
        self.assertIn("Нет доступа", cb.answer.await_args.args[0])

    async def test_gifts_listed_and_cached_in_state(self) -> None:
        cb = _callback()
        state = _state()
        bot = SimpleNamespace(
            get_available_gifts=AsyncMock(return_value=SimpleNamespace(gifts=[_gift("g1", 15), _gift("g2", 25)]))
        )
        await cb_admin_gift(cb, _admin(), state, bot)
        cb.message.edit_text.assert_awaited_once()
        data = await state.get_data()
        self.assertEqual(data["gifts_map"], {"g1": 15, "g2": 25})


class GiftPickTests(DbTestCase):
    async def test_pick_stores_gift_and_prompts_description(self) -> None:
        cb = _callback(data="admin:gift_pick:g1")
        state = _state()
        await state.update_data(gifts_map={"g1": 15})
        await cb_gift_pick(cb, _admin(), state)
        data = await state.get_data()
        self.assertEqual(data["gift_id"], "g1")
        self.assertEqual(data["star_count"], 15)
        self.assertEqual(await state.get_state(), AdminGiftStates.enter_description)

    async def test_pick_with_stale_state_is_rejected(self) -> None:
        cb = _callback(data="admin:gift_pick:unknown")
        state = _state()
        await cb_gift_pick(cb, _admin(), state)
        cb.answer.assert_awaited_once()
        self.assertIn("устарел", cb.answer.await_args.args[0])


class GiftDescriptionTests(DbTestCase):
    async def test_dash_skips_description(self) -> None:
        msg = _message("-")
        state = _state()
        await msg_gift_description(msg, state, _admin())
        data = await state.get_data()
        self.assertEqual(data["description"], "")
        self.assertEqual(await state.get_state(), AdminGiftStates.enter_target)

    async def test_too_long_description_is_rejected(self) -> None:
        msg = _message("x" * 200)
        state = _state()
        await msg_gift_description(msg, state, _admin())
        self.assertIsNone(await state.get_state())
        msg.answer.assert_awaited_once()
        self.assertIn("Слишком длинный", msg.answer.await_args.args[0])


class GiftTargetTests(DbTestCase):
    async def test_resolves_target_by_numeric_id_in_db(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=42, first_name="Target", username="targetuser"))
            await session.commit()
            state = _state()
            await state.update_data(gift_id="g1", star_count=15, description="hi")
            msg = _message("42")
            await msg_gift_target(msg, state, session, _admin(), SimpleNamespace())
        data = await state.get_data()
        self.assertEqual(data["target_id"], 42)
        self.assertEqual(await state.get_state(), AdminGiftStates.confirm)
        msg.answer.assert_awaited_once()
        self.assertIn("Подтверди", msg.answer.await_args.args[0])

    async def test_resolves_target_by_username_in_db(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=42, first_name="Target", username="targetuser"))
            await session.commit()
            state = _state()
            await state.update_data(gift_id="g1", star_count=15, description="")
            msg = _message("@targetuser")
            await msg_gift_target(msg, state, session, _admin(), SimpleNamespace())
        data = await state.get_data()
        self.assertEqual(data["target_id"], 42)

    async def test_unknown_target_is_rejected(self) -> None:
        async with self.sessions() as session:
            state = _state()
            await state.update_data(gift_id="g1", star_count=15, description="")
            bot = SimpleNamespace(get_chat=AsyncMock(side_effect=TelegramBadRequest(method=SimpleNamespace(), message="not found")))
            msg = _message("@doesnotexist")
            await msg_gift_target(msg, state, session, _admin(), bot)
        self.assertIsNone(await state.get_state())
        msg.answer.assert_awaited_once()
        self.assertIn("не найден", msg.answer.await_args.args[0])


class GiftSendTests(DbTestCase):
    async def test_confirm_sends_gift_and_reports_success(self) -> None:
        cb = _callback()
        state = _state()
        await state.set_state(AdminGiftStates.confirm)
        await state.update_data(gift_id="g1", star_count=15, target_id=42, description="hi")
        bot = SimpleNamespace(send_gift=AsyncMock(return_value=True))
        await cb_gift_send(cb, _admin(), state, bot)
        bot.send_gift.assert_awaited_once_with(gift_id="g1", user_id=42, text="hi")
        self.assertIsNone(await state.get_state())
        cb.message.edit_text.assert_awaited_once()
        self.assertIn("✔️", cb.message.edit_text.await_args.args[0])

    async def test_send_failure_shown_to_admin(self) -> None:
        cb = _callback()
        state = _state()
        await state.set_state(AdminGiftStates.confirm)
        await state.update_data(gift_id="g1", star_count=15, target_id=42, description="")
        bot = SimpleNamespace(
            send_gift=AsyncMock(
                side_effect=TelegramBadRequest(method=SimpleNamespace(), message="BALANCE_TOO_LOW")
            )
        )
        await cb_gift_send(cb, _admin(), state, bot)
        cb.message.edit_text.assert_awaited_once()
        self.assertIn("Не удалось", cb.message.edit_text.await_args.args[0])


class GiftCancelTests(DbTestCase):
    async def test_cancel_clears_state(self) -> None:
        cb = _callback()
        state = _state()
        await state.set_state(AdminGiftStates.confirm)
        await state.update_data(gift_id="g1")
        await cb_gift_cancel(cb, _admin(), state)
        self.assertIsNone(await state.get_state())


if __name__ == "__main__":
    unittest.main()
