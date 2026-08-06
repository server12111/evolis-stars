import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User, Withdrawal
from bot.handlers.withdraw import (
    cb_withdraw_amount,
    cb_withdraw_confirm,
    cb_withdraw_method,
    cb_withdraw_recipient_other,
    cb_withdraw_recipient_self,
    cb_withdraw_stars_menu,
    msg_captcha,
    msg_recipient_username,
)
from bot.states.withdraw import WithdrawStates


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _callback(data: str):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, data=data, answer=AsyncMock())


def _message(text: str):
    return SimpleNamespace(text=text, answer=AsyncMock())


_SETTINGS_PATCHES = (
    patch("bot.handlers.withdraw.SettingsRepository.get_bool", AsyncMock(return_value=True)),
    patch("bot.handlers.withdraw.SettingsRepository.get", AsyncMock(return_value="15,25,50,100")),
    patch("bot.handlers.withdraw.SettingsRepository.get_float", AsyncMock(return_value=15.0)),
)


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class MenuNoLongerGatedOnUsernameTests(ChatModelsTestCase):
    async def test_menu_shows_amounts_even_without_username(self) -> None:
        db_user = SimpleNamespace(user_id=1, username=None, stars_balance=Decimal("50"))
        cb = _callback("menu:withdraw")
        state = SimpleNamespace(clear=AsyncMock())
        with _SETTINGS_PATCHES[0], _SETTINGS_PATCHES[1], _SETTINGS_PATCHES[2], \
                patch("bot.handlers.withdraw.ContentRepository.get_photo", AsyncMock(return_value=None)):
            async with self.sessions() as session:
                await cb_withdraw_stars_menu(cb, db_user, session, state)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertNotIn("Username", rendered)
        self.assertIn("сумму", rendered.lower())


class SelfWithdrawalTests(ChatModelsTestCase):
    async def test_full_flow_creates_withdrawal_for_own_username(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=10, username="myself", first_name="U", stars_balance=Decimal("100")))
            await session.commit()

        state = _state()
        db_user = SimpleNamespace(user_id=10, username="myself", stars_balance=Decimal("100"), is_vip=False, first_name="U")

        # 1. Pick amount
        cb1 = _callback("withdraw:amount:15")
        with _SETTINGS_PATCHES[0], _SETTINGS_PATCHES[1], _SETTINGS_PATCHES[2]:
            async with self.sessions() as session:
                await cb_withdraw_amount(cb1, db_user, state, session)
        self.assertEqual(await state.get_state(), WithdrawStates.choose_recipient)

        # 2. Choose "self"
        cb2 = _callback("withdraw:recipient:self")
        await cb_withdraw_recipient_self(cb2, db_user, state)
        self.assertEqual(await state.get_state(), WithdrawStates.confirm)
        rendered = cb2.message.edit_text.await_args.args[0]
        self.assertIn("@myself", rendered)

        # 3. Confirm -> captcha shown
        cb3 = _callback("withdraw:confirm")
        with patch("bot.handlers.withdraw.generate_captcha", return_value=("2 + 2", 4)):
            await cb_withdraw_confirm(cb3, state)
        self.assertEqual(await state.get_state(), WithdrawStates.enter_captcha)

        # 4. Answer captcha -> withdrawal created
        message = _message("4")
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        with _SETTINGS_PATCHES[0], _SETTINGS_PATCHES[1], _SETTINGS_PATCHES[2], \
                patch("bot.handlers.withdraw.settings.admin_channel_id", "123"), \
                patch("bot.handlers.withdraw.settings.payments_channel_id", ""), \
                patch("bot.handlers.withdraw.settings.payments_channel_link", ""):
            async with self.sessions() as session:
                real_user = await session.get(User, 10)
                await msg_captcha(message, real_user, session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 10)
            result = await session.execute(select(Withdrawal))
            withdrawal = result.scalars().one()
        self.assertEqual(float(saved_user.stars_balance), 55.0)
        self.assertEqual(withdrawal.recipient_username, "myself")
        self.assertEqual(withdrawal.user_id, 10)

    async def test_self_without_username_is_blocked_no_withdrawal(self) -> None:
        db_user = SimpleNamespace(user_id=11, username=None, stars_balance=Decimal("50"))
        state = _state()
        await state.set_state(WithdrawStates.choose_recipient)
        await state.update_data(amount=15)

        cb = _callback("withdraw:recipient:self")
        await cb_withdraw_recipient_self(cb, db_user, state)

        # Stays on the recipient-choice step — no confirm, no captcha.
        self.assertEqual(await state.get_state(), WithdrawStates.choose_recipient)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("username", rendered.lower())


class OtherUserWithdrawalTests(ChatModelsTestCase):
    async def test_full_flow_with_at_sign_creates_withdrawal_for_recipient(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=20, username="sender", first_name="U", stars_balance=Decimal("100")))
            await session.commit()

        state = _state()
        db_user = SimpleNamespace(user_id=20, username="sender", stars_balance=Decimal("100"), is_vip=False, first_name="U")

        cb1 = _callback("withdraw:amount:15")
        with _SETTINGS_PATCHES[0], _SETTINGS_PATCHES[1], _SETTINGS_PATCHES[2]:
            async with self.sessions() as session:
                await cb_withdraw_amount(cb1, db_user, state, session)

        cb2 = _callback("withdraw:recipient:other")
        await cb_withdraw_recipient_other(cb2, state)
        self.assertEqual(await state.get_state(), WithdrawStates.enter_recipient_username)

        msg = _message("@Some_Recipient")
        await msg_recipient_username(msg, db_user, state)
        self.assertEqual(await state.get_state(), WithdrawStates.confirm)
        rendered = msg.answer.await_args.args[0]
        self.assertIn("@Some_Recipient", rendered)
        data = await state.get_data()
        self.assertEqual(data["recipient_username"], "Some_Recipient")

        cb3 = _callback("withdraw:confirm")
        with patch("bot.handlers.withdraw.generate_captcha", return_value=("2 + 2", 4)):
            await cb_withdraw_confirm(cb3, state)

        message = _message("4")
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        with _SETTINGS_PATCHES[0], _SETTINGS_PATCHES[1], _SETTINGS_PATCHES[2], \
                patch("bot.handlers.withdraw.settings.admin_channel_id", "123"), \
                patch("bot.handlers.withdraw.settings.payments_channel_id", ""), \
                patch("bot.handlers.withdraw.settings.payments_channel_link", ""):
            async with self.sessions() as session:
                real_user = await session.get(User, 20)
                await msg_captcha(message, real_user, session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 20)
            result = await session.execute(select(Withdrawal))
            withdrawal = result.scalars().one()
        # The REQUESTER's own balance is debited, regardless of recipient.
        self.assertEqual(float(saved_user.stars_balance), 55.0)
        self.assertEqual(withdrawal.recipient_username, "Some_Recipient")
        self.assertEqual(withdrawal.user_id, 20)

    async def test_username_without_at_sign_is_normalized(self) -> None:
        db_user = SimpleNamespace(user_id=21, username="sender", stars_balance=Decimal("50"))
        state = _state()
        await state.set_state(WithdrawStates.enter_recipient_username)
        await state.update_data(amount=15)

        msg = _message("plain_username")
        await msg_recipient_username(msg, db_user, state)

        data = await state.get_data()
        self.assertEqual(data["recipient_username"], "plain_username")

    async def test_invalid_username_format_rejected_and_can_retry(self) -> None:
        db_user = SimpleNamespace(user_id=22, username="sender", stars_balance=Decimal("50"))
        state = _state()
        await state.set_state(WithdrawStates.enter_recipient_username)
        await state.update_data(amount=15)

        msg = _message("ab")  # too short — Telegram usernames need 5+ chars
        await msg_recipient_username(msg, db_user, state)

        self.assertEqual(await state.get_state(), WithdrawStates.enter_recipient_username)
        rendered = msg.answer.await_args.args[0]
        self.assertIn("Некорректный", rendered)

    async def test_invalid_username_with_spaces_rejected(self) -> None:
        db_user = SimpleNamespace(user_id=23, username="sender", stars_balance=Decimal("50"))
        state = _state()
        await state.set_state(WithdrawStates.enter_recipient_username)
        await state.update_data(amount=15)

        msg = _message("not a username")
        await msg_recipient_username(msg, db_user, state)
        self.assertEqual(await state.get_state(), WithdrawStates.enter_recipient_username)

    async def test_cannot_gift_to_own_username(self) -> None:
        db_user = SimpleNamespace(user_id=24, username="sender", stars_balance=Decimal("50"))
        state = _state()
        await state.set_state(WithdrawStates.enter_recipient_username)
        await state.update_data(amount=15)

        msg = _message("@Sender")  # same as own, different case
        await msg_recipient_username(msg, db_user, state)

        self.assertEqual(await state.get_state(), WithdrawStates.enter_recipient_username)
        rendered = msg.answer.await_args.args[0]
        self.assertIn("собственный", rendered)


class MethodChoiceTests(ChatModelsTestCase):
    async def test_below_threshold_skips_method_choice_and_defaults_to_fragment(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=30, username="u30", first_name="U", stars_balance=Decimal("100")))
            await session.commit()

        state = _state()
        db_user = SimpleNamespace(user_id=30, username="u30", stars_balance=Decimal("100"), is_vip=False, first_name="U")

        cb2 = _callback("withdraw:recipient:self")
        await state.update_data(amount=25)
        await cb_withdraw_recipient_self(cb2, db_user, state)

        self.assertEqual(await state.get_state(), WithdrawStates.confirm)
        rendered = cb2.message.edit_text.await_args.args[0]
        self.assertNotIn("Способ", rendered)
        data = await state.get_data()
        self.assertEqual(data["withdrawal_method"], "fragment")

    async def test_50_or_more_asks_for_method_and_stores_it(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=31, username="u31", first_name="U", stars_balance=Decimal("200")))
            await session.commit()

        state = _state()
        db_user = SimpleNamespace(user_id=31, username="u31", stars_balance=Decimal("200"), is_vip=False, first_name="U")

        cb2 = _callback("withdraw:recipient:self")
        await state.update_data(amount=50)
        await cb_withdraw_recipient_self(cb2, db_user, state)
        self.assertEqual(await state.get_state(), WithdrawStates.choose_method)
        self.assertIn("способ", cb2.message.edit_text.await_args.args[0].lower())

        cb3 = _callback("withdraw:method:gift")
        await cb_withdraw_method(cb3, state)
        self.assertEqual(await state.get_state(), WithdrawStates.confirm)
        rendered = cb3.message.edit_text.await_args.args[0]
        self.assertIn("Подарком", rendered)
        data = await state.get_data()
        self.assertEqual(data["withdrawal_method"], "gift")

        cb4 = _callback("withdraw:confirm")
        with patch("bot.handlers.withdraw.generate_captcha", return_value=("2 + 2", 4)):
            await cb_withdraw_confirm(cb4, state)

        message = _message("4")
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        with _SETTINGS_PATCHES[0], _SETTINGS_PATCHES[1], _SETTINGS_PATCHES[2], \
                patch("bot.handlers.withdraw.settings.admin_channel_id", "123"), \
                patch("bot.handlers.withdraw.settings.payments_channel_id", ""), \
                patch("bot.handlers.withdraw.settings.payments_channel_link", ""):
            async with self.sessions() as session:
                real_user = await session.get(User, 31)
                await msg_captcha(message, real_user, session, state, bot)

        async with self.sessions() as session:
            result = await session.execute(select(Withdrawal))
            withdrawal = result.scalars().one()
        self.assertEqual(withdrawal.withdrawal_method, "gift")

        admin_text = bot.send_message.await_args_list[0].args[1]
        self.assertIn("Способ вывода", admin_text)
        self.assertIn("Подарок", admin_text)

    async def test_below_threshold_admin_message_omits_method_and_rp_debited_line(self) -> None:
        """A 15-star withdrawal never asks for a method — the admin channel
        message must not claim one was chosen (it was silently defaulted),
        and must not expose the internal RP⭐️ debit at all."""
        async with self.sessions() as session:
            session.add(User(user_id=33, username="u33", first_name="U", stars_balance=Decimal("100")))
            await session.commit()

        state = _state()
        db_user = SimpleNamespace(user_id=33, username="u33", stars_balance=Decimal("100"), is_vip=False, first_name="U")

        cb2 = _callback("withdraw:recipient:self")
        await state.update_data(amount=15)
        await cb_withdraw_recipient_self(cb2, db_user, state)
        self.assertEqual(await state.get_state(), WithdrawStates.confirm)

        cb3 = _callback("withdraw:confirm")
        with patch("bot.handlers.withdraw.generate_captcha", return_value=("2 + 2", 4)):
            await cb_withdraw_confirm(cb3, state)

        message = _message("4")
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        with _SETTINGS_PATCHES[0], _SETTINGS_PATCHES[1], _SETTINGS_PATCHES[2], \
                patch("bot.handlers.withdraw.settings.admin_channel_id", "123"), \
                patch("bot.handlers.withdraw.settings.payments_channel_id", ""), \
                patch("bot.handlers.withdraw.settings.payments_channel_link", ""):
            async with self.sessions() as session:
                real_user = await session.get(User, 33)
                await msg_captcha(message, real_user, session, state, bot)

        admin_text = bot.send_message.await_args_list[0].args[1]
        self.assertNotIn("Способ вывода", admin_text)
        self.assertNotIn("Списано", admin_text)
        self.assertNotIn("RP⭐️", admin_text)

        success_text = message.answer.await_args.args[0]
        self.assertNotIn("Списано", success_text)
        self.assertNotIn("RP⭐️", success_text)

    async def test_unknown_method_value_is_rejected(self) -> None:
        state = _state()
        await state.set_state(WithdrawStates.choose_method)
        await state.update_data(amount=50, recipient_username="u32")

        cb = _callback("withdraw:method:bogus")
        await cb_withdraw_method(cb, state)

        self.assertEqual(await state.get_state(), WithdrawStates.choose_method)
        cb.message.edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
