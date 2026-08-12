import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import CryptoWithdrawal, User
from bot.database.repositories.crypto_withdrawal import CryptoWithdrawalRepository
from bot.handlers.admin_channel import cb_cryptowithdraw_approve, cb_cryptowithdraw_reject
from bot.handlers.crypto_withdraw import (
    _normalize_ton_address,
    _parse_rp_amount,
    cb_crypto_amount,
    cb_crypto_confirm,
    cb_crypto_method,
    cb_withdraw_crypto_menu,
    msg_crypto_amount_custom,
    msg_crypto_recipient,
)
from bot.states.crypto_withdraw import CryptoWithdrawStates


def callback(data: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
    )


def message(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, answer=AsyncMock())


class FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self._state = None
        self._data: dict = dict(data or {})

    async def set_state(self, state) -> None:
        self._state = state

    async def clear(self) -> None:
        self._state = None
        self._data = {}

    async def get_data(self) -> dict:
        return dict(self._data)

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)


def db_user(**kwargs) -> SimpleNamespace:
    base = dict(user_id=1, username="tester", stars_balance=Decimal("1000"), is_vip=False, referral_tier=None)
    base.update(kwargs)
    return SimpleNamespace(**base)


def db_user_row(user_id: int, balance: str = "1000") -> User:
    return User(user_id=user_id, username="tester", first_name="T", stars_balance=Decimal(balance))


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class ParseRpAmountTests(unittest.TestCase):
    def test_plain_digits(self) -> None:
        self.assertEqual(_parse_rp_amount("100"), 100)

    def test_strips_spaces_and_commas(self) -> None:
        self.assertEqual(_parse_rp_amount("1 000"), 1000)
        self.assertEqual(_parse_rp_amount("1,000"), 1000)
        # Non-breaking space -- what Telegram clients actually insert as a
        # thousands separator when a user copy-pastes a formatted amount.
        self.assertEqual(_parse_rp_amount("1\xa0000"), 1000)

    def test_non_numeric_returns_none(self) -> None:
        self.assertIsNone(_parse_rp_amount("abc"))
        self.assertIsNone(_parse_rp_amount("50.5"))
        self.assertIsNone(_parse_rp_amount(""))


class TonAddressValidationTests(unittest.TestCase):
    def test_valid_length_accepted(self) -> None:
        addr = "UQ" + "A" * 46
        self.assertEqual(_normalize_ton_address(addr), addr)

    def test_strips_surrounding_whitespace(self) -> None:
        addr = "UQ" + "A" * 46
        self.assertEqual(_normalize_ton_address(f"  {addr}  "), addr)

    def test_wrong_length_rejected(self) -> None:
        self.assertIsNone(_normalize_ton_address("tooshort"))

    def test_invalid_characters_rejected(self) -> None:
        self.assertIsNone(_normalize_ton_address("!" * 48))


class CryptoMenuTests(DbTestCase):
    async def test_menu_shows_balance_and_rate(self) -> None:
        cb = callback()
        state = FakeState()
        async with self.sessions() as session:
            await cb_withdraw_crypto_menu(cb, db_user(stars_balance=Decimal("42.50")), session, state)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("42.50", rendered)
        self.assertIn("0.005", rendered)

    async def test_disabled_shows_alert_not_menu(self) -> None:
        cb = callback()
        state = FakeState()
        async with self.sessions() as session:
            from bot.database.repositories.settings import SettingsRepository
            await SettingsRepository(session).set("withdraw_crypto_enabled", "0")
            await cb_withdraw_crypto_menu(cb, db_user(), session, state)

        cb.message.edit_text.assert_not_awaited()
        cb.answer.assert_awaited_once()
        self.assertIn("недоступен", cb.answer.await_args.args[0])


class CryptoMethodChoiceTests(DbTestCase):
    async def test_choosing_usdt_send_shows_amounts(self) -> None:
        cb = callback("cryptowithdraw:method:usdt_send")
        state = FakeState()
        async with self.sessions() as session:
            await cb_crypto_method(cb, session, state)
        self.assertEqual(state._state, CryptoWithdrawStates.enter_amount)
        self.assertEqual(state._data["crypto_method"], "usdt_send")

    async def test_choosing_ton_shows_amounts(self) -> None:
        cb = callback("cryptowithdraw:method:ton")
        state = FakeState()
        async with self.sessions() as session:
            await cb_crypto_method(cb, session, state)
        self.assertEqual(state._data["crypto_method"], "ton")

    async def test_unknown_method_rejected(self) -> None:
        cb = callback("cryptowithdraw:method:bogus")
        state = FakeState()
        async with self.sessions() as session:
            await cb_crypto_method(cb, session, state)
        cb.answer.assert_awaited_once()
        self.assertIn("Неверный", cb.answer.await_args.args[0])
        self.assertIsNone(state._state)


class CryptoAmountSelectionTests(DbTestCase):
    async def test_usdt_amount_below_minimum_is_rejected(self) -> None:
        state = FakeState({"crypto_method": "usdt_send"})
        cb = callback("cryptowithdraw:amount:10")
        async with self.sessions() as session:
            await cb_crypto_amount(cb, db_user(), session, state)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("от 50", rendered)
        self.assertEqual(state._state, None)

    async def test_usdt_amount_within_range_advances_to_recipient(self) -> None:
        state = FakeState({"crypto_method": "usdt_send"})
        cb = callback("cryptowithdraw:amount:100")
        async with self.sessions() as session:
            await cb_crypto_amount(cb, db_user(), session, state)
        self.assertEqual(state._state, CryptoWithdrawStates.enter_recipient)
        # 100 RP * 0.005 = 0.5 $
        self.assertEqual(Decimal(state._data["crypto_payout_amount"]), Decimal("0.5"))
        self.assertEqual(state._data["crypto_ton_rate"], "")

    async def test_insufficient_balance_is_rejected(self) -> None:
        state = FakeState({"crypto_method": "usdt_send"})
        cb = callback("cryptowithdraw:amount:500")
        async with self.sessions() as session:
            await cb_crypto_amount(cb, db_user(stars_balance=Decimal("10")), session, state)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Недостаточно", rendered)
        self.assertIsNone(state._state)

    async def test_ton_amount_computes_payout_from_live_rate(self) -> None:
        state = FakeState({"crypto_method": "ton"})
        cb = callback("cryptowithdraw:amount:100")
        with patch("bot.handlers.crypto_withdraw.get_ton_usd_rate", AsyncMock(return_value=5.0)):
            async with self.sessions() as session:
                await cb_crypto_amount(cb, db_user(), session, state)
        self.assertEqual(state._state, CryptoWithdrawStates.enter_recipient)
        # 100 RP * 0.005 $/RP = 0.5$; 0.5$ / 5.0 $/TON = 0.1 TON
        self.assertEqual(Decimal(state._data["crypto_payout_amount"]), Decimal("0.1"))
        self.assertEqual(state._data["crypto_ton_rate"], "5.0")

    async def test_ton_rate_unavailable_blocks_without_guessing(self) -> None:
        """Must never fall back to a stale/made-up rate for a real payout —
        the whole point of fetching live is that a wrong rate either
        shortchanges the user or overpays them out of pocket."""
        state = FakeState({"crypto_method": "ton"})
        cb = callback("cryptowithdraw:amount:100")
        with patch("bot.handlers.crypto_withdraw.get_ton_usd_rate", AsyncMock(return_value=None)):
            async with self.sessions() as session:
                await cb_crypto_amount(cb, db_user(), session, state)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("недоступен", rendered)
        self.assertIsNone(state._state)  # never advanced


class CryptoRecipientTests(DbTestCase):
    async def test_usdt_send_accepts_valid_username(self) -> None:
        state = FakeState({
            "crypto_method": "usdt_send", "crypto_rp_amount": 100, "crypto_payout_amount": "0.5",
            "crypto_ton_rate": "",
        })
        msg = message("some_user")
        async with self.sessions() as session:
            await msg_crypto_recipient(msg, db_user(), session, state)
        self.assertEqual(state._state, CryptoWithdrawStates.confirm)
        self.assertEqual(state._data["crypto_recipient"], "some_user")
        rendered = msg.answer.await_args.args[0]
        self.assertIn("@some_user", rendered)

    async def test_usdt_send_rejects_invalid_username(self) -> None:
        state = FakeState({
            "crypto_method": "usdt_send", "crypto_rp_amount": 100, "crypto_payout_amount": "0.5",
            "crypto_ton_rate": "",
        })
        msg = message("!!")
        async with self.sessions() as session:
            await msg_crypto_recipient(msg, db_user(), session, state)
        self.assertIn("Неверный username", msg.answer.await_args.args[0])
        self.assertIsNone(state._state)

    async def test_ton_accepts_valid_wallet_address(self) -> None:
        addr = "UQ" + "B" * 46
        state = FakeState({
            "crypto_method": "ton", "crypto_rp_amount": 100, "crypto_payout_amount": "0.1",
            "crypto_ton_rate": "5.0",
        })
        msg = message(addr)
        async with self.sessions() as session:
            await msg_crypto_recipient(msg, db_user(), session, state)
        self.assertEqual(state._data["crypto_recipient"], addr)
        rendered = msg.answer.await_args.args[0]
        self.assertIn(addr, rendered)

    async def test_ton_rejects_invalid_wallet_address(self) -> None:
        state = FakeState({
            "crypto_method": "ton", "crypto_rp_amount": 100, "crypto_payout_amount": "0.1",
            "crypto_ton_rate": "5.0",
        })
        msg = message("not-a-wallet")
        async with self.sessions() as session:
            await msg_crypto_recipient(msg, db_user(), session, state)
        self.assertIn("Неверный адрес", msg.answer.await_args.args[0])
        self.assertIsNone(state._state)


class CryptoFinalizeTests(DbTestCase):
    def _confirmed_usdt_state(self) -> FakeState:
        return FakeState({
            "crypto_method": "usdt_send", "crypto_rp_amount": 100,
            "crypto_payout_amount": "0.5", "crypto_ton_rate": "", "crypto_recipient": "some_user",
        })

    async def test_successful_withdrawal_debits_and_creates_row(self) -> None:
        cb = callback("cryptowithdraw:confirm")
        state = self._confirmed_usdt_state()
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)))
        async with self.sessions() as session:
            session.add(db_user_row(10, balance="1000"))
            await session.commit()
            with (
                patch("bot.handlers.crypto_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.crypto_withdraw.settings.payments_channel_id", ""),
            ):
                await cb_crypto_confirm(cb, db_user(user_id=10), session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 10)
            withdrawals = (await session.execute(select(CryptoWithdrawal))).scalars().all()

        self.assertEqual(float(saved_user.stars_balance), 900.0)  # 1000 - 100
        self.assertEqual(len(withdrawals), 1)
        self.assertEqual(withdrawals[0].method, "usdt_send")
        self.assertEqual(float(withdrawals[0].payout_amount), 0.5)
        self.assertEqual(withdrawals[0].recipient, "some_user")
        self.assertEqual(withdrawals[0].status, "pending")

    async def test_admin_channel_not_configured_does_not_debit(self) -> None:
        cb = callback("cryptowithdraw:confirm")
        state = self._confirmed_usdt_state()
        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            session.add(db_user_row(11))
            await session.commit()
            with patch("bot.handlers.crypto_withdraw.settings.admin_channel_id", ""):
                await cb_crypto_confirm(cb, db_user(user_id=11), session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 11)
        self.assertEqual(float(saved_user.stars_balance), 1000.0)

    async def test_admin_send_failure_refunds_in_full(self) -> None:
        cb = callback("cryptowithdraw:confirm")
        state = self._confirmed_usdt_state()
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("offline")))
        async with self.sessions() as session:
            session.add(db_user_row(12))
            await session.commit()
            with (
                patch("bot.handlers.crypto_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.crypto_withdraw.settings.payments_channel_id", ""),
            ):
                await cb_crypto_confirm(cb, db_user(user_id=12), session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 12)
            count = (await session.execute(select(func.count(CryptoWithdrawal.id)))).scalar_one()
        self.assertEqual(float(saved_user.stars_balance), 1000.0)  # debited then refunded
        self.assertEqual(count, 0)

    async def test_public_post_omits_recipient_but_admin_post_includes_it(self) -> None:
        cb = callback("cryptowithdraw:confirm")
        state = self._confirmed_usdt_state()
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[
            SimpleNamespace(message_id=77),  # payments channel
            SimpleNamespace(message_id=88),  # admin channel
        ]))
        async with self.sessions() as session:
            session.add(db_user_row(14))
            await session.commit()
            with (
                patch("bot.handlers.crypto_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.crypto_withdraw.settings.payments_channel_id", "-100888"),
            ):
                await cb_crypto_confirm(cb, db_user(user_id=14), session, state, bot)

        self.assertEqual(bot.send_message.await_count, 2)
        payments_call, admin_call = bot.send_message.await_args_list
        self.assertEqual(payments_call.args[0], -100888)
        self.assertEqual(admin_call.args[0], -100999)
        self.assertNotIn("some_user", payments_call.args[1])
        self.assertIn("some_user", admin_call.args[1])
        self.assertIn("#1", payments_call.args[1])
        self.assertIn("#1", admin_call.args[1])

        async with self.sessions() as session:
            withdrawals = (await session.execute(select(CryptoWithdrawal))).scalars().all()
        self.assertEqual(withdrawals[0].channel_message_id, 77)
        self.assertEqual(withdrawals[0].admin_message_id, 88)
        self.assertEqual(withdrawals[0].display_number, 1)

    async def test_amount_below_current_minimum_after_admin_raise_is_blocked(self) -> None:
        cb = callback("cryptowithdraw:confirm")
        state = self._confirmed_usdt_state()  # rp_amount = 100
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        async with self.sessions() as session:
            session.add(db_user_row(13))
            await session.commit()
            from bot.database.repositories.settings import SettingsRepository
            await SettingsRepository(session).set("crypto_min_withdrawal", "150")
            with patch("bot.handlers.crypto_withdraw.settings.admin_channel_id", "-100999"):
                await cb_crypto_confirm(cb, db_user(user_id=13), session, state, bot)

        bot.send_message.assert_not_awaited()
        async with self.sessions() as session:
            saved_user = await session.get(User, 13)
            count = (await session.execute(select(func.count(CryptoWithdrawal.id)))).scalar_one()
        self.assertEqual(float(saved_user.stars_balance), 1000.0)  # never debited
        self.assertEqual(count, 0)

    async def test_stale_state_missing_amount_is_a_clean_no_op(self) -> None:
        cb = callback("cryptowithdraw:confirm")
        state = FakeState()  # nothing set
        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            await cb_crypto_confirm(cb, db_user(), session, state, bot)
        bot.send_message.assert_not_awaited()
        cb.answer.assert_awaited_once()


class CryptoAdminApproveRejectTests(DbTestCase):
    async def test_approve_marks_status_and_notifies_user_with_recipient(self) -> None:
        async with self.sessions() as session:
            session.add(db_user_row(20))
            await session.commit()
            w = await CryptoWithdrawalRepository(session).create(
                20, "usdt_send", Decimal(100), Decimal("0.5"), None, "some_user",
            )
            await session.commit()
            wid = w.id

        cb = SimpleNamespace(
            data=f"admin:cryptowithdraw_approve:{wid}",
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(edit_text=AsyncMock(), text="request"),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            with patch("bot.handlers.admin_channel.settings.admin_ids", "999"):
                await cb_cryptowithdraw_approve(cb, session)

        async with self.sessions() as session:
            w = await session.get(CryptoWithdrawal, wid)
        self.assertEqual(w.status, "approved")
        cb.bot.send_message.assert_awaited_once()
        self.assertIn("some_user", cb.bot.send_message.await_args.args[1])

    async def test_reject_marks_status_and_tells_user_not_refunded(self) -> None:
        async with self.sessions() as session:
            session.add(db_user_row(21))
            await session.commit()
            w = await CryptoWithdrawalRepository(session).create(
                21, "ton", Decimal(100), Decimal("0.1"), Decimal("5.0"), "UQ" + "C" * 46,
            )
            await session.commit()
            wid = w.id

        cb = SimpleNamespace(
            data=f"admin:cryptowithdraw_reject:{wid}",
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(edit_text=AsyncMock(), text="request"),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            with patch("bot.handlers.admin_channel.settings.admin_ids", "999"):
                await cb_cryptowithdraw_reject(cb, session)

        async with self.sessions() as session:
            w = await session.get(CryptoWithdrawal, wid)
        self.assertEqual(w.status, "rejected")
        cb.bot.send_message.assert_awaited_once()
        self.assertIn("не возвращены", cb.bot.send_message.await_args.args[1])

    async def test_non_admin_cannot_approve(self) -> None:
        async with self.sessions() as session:
            session.add(db_user_row(22))
            await session.commit()
            w = await CryptoWithdrawalRepository(session).create(
                22, "usdt_send", Decimal(100), Decimal("0.5"), None, "some_user",
            )
            await session.commit()
            wid = w.id

        cb = SimpleNamespace(
            data=f"admin:cryptowithdraw_approve:{wid}",
            from_user=SimpleNamespace(id=1),  # not an admin
            message=SimpleNamespace(edit_text=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            with patch("bot.handlers.admin_channel.settings.admin_ids", "999"):
                await cb_cryptowithdraw_approve(cb, session)

        async with self.sessions() as session:
            w = await session.get(CryptoWithdrawal, wid)
        self.assertEqual(w.status, "pending")
        cb.bot.send_message.assert_not_awaited()

    async def test_already_processed_cannot_be_approved_again(self) -> None:
        async with self.sessions() as session:
            session.add(db_user_row(23))
            await session.commit()
            w = await CryptoWithdrawalRepository(session).create(
                23, "usdt_send", Decimal(100), Decimal("0.5"), None, "some_user",
            )
            await session.commit()
            wid = w.id
            await CryptoWithdrawalRepository(session).approve(wid)

        cb = SimpleNamespace(
            data=f"admin:cryptowithdraw_approve:{wid}",
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(edit_text=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            with patch("bot.handlers.admin_channel.settings.admin_ids", "999"):
                await cb_cryptowithdraw_approve(cb, session)

        cb.answer.assert_awaited_once()
        self.assertIn("уже обработана", cb.answer.await_args.args[0])
        cb.bot.send_message.assert_not_awaited()


class WithdrawMethodChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_top_level_menu_offers_crypto(self) -> None:
        from bot.handlers.withdraw import cb_withdraw_menu

        cb = callback()
        state = FakeState()
        await cb_withdraw_menu(cb, state)

        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        self.assertIn("withdraw:choose:crypto", callback_datas)


if __name__ == "__main__":
    unittest.main()
