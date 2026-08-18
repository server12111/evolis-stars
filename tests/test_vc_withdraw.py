import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User, VcWithdrawal
from bot.database.repositories.vc_withdrawal import VcWithdrawalRepository
from bot.handlers.admin_channel import cb_vcwithdraw_approve, cb_vcwithdraw_reject
from bot.handlers.vc_withdraw import (
    _parse_vc_amount,
    _rp_cost_for_vc,
    _vc_rate_for_amount,
    cb_vc_amount,
    cb_vc_check_sub,
    cb_vc_confirm,
    cb_withdraw_vc_menu,
    msg_vc_amount_custom,
)
from bot.services.chat_eligibility import credit_stars
from bot.states.vc_withdraw import VcWithdrawStates


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


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class VcRateInterpolationTests(DbTestCase):
    async def test_min_amount_gets_the_floor_rate(self) -> None:
        async with self.sessions() as session:
            rate = await _vc_rate_for_amount(session, 10_000)
        self.assertEqual(rate, Decimal("400.00"))

    async def test_max_amount_gets_the_ceiling_rate(self) -> None:
        async with self.sessions() as session:
            rate = await _vc_rate_for_amount(session, 500_000)
        self.assertEqual(rate, Decimal("800.00"))

    async def test_midpoint_amount_gets_the_midpoint_rate(self) -> None:
        # 255000 is exactly halfway between 10000 and 500000.
        async with self.sessions() as session:
            rate = await _vc_rate_for_amount(session, 255_000)
        self.assertEqual(rate, Decimal("600.00"))

    async def test_amount_above_max_is_clamped_to_the_ceiling_rate(self) -> None:
        # Shouldn't normally happen (max withdrawal is enforced before this
        # is ever called) -- must still degrade gracefully, not error.
        async with self.sessions() as session:
            rate = await _vc_rate_for_amount(session, 600_000)
        self.assertEqual(rate, Decimal("800.00"))

    async def test_admin_configured_bounds_are_honored(self) -> None:
        async with self.sessions() as session:
            from bot.database.repositories.settings import SettingsRepository
            repo = SettingsRepository(session)
            await repo.set("vc_rate_min", "100")
            await repo.set("vc_rate_max", "300")
            rate_at_min = await _vc_rate_for_amount(session, 10_000)
            rate_at_max = await _vc_rate_for_amount(session, 500_000)
        self.assertEqual(rate_at_min, Decimal("100.00"))
        self.assertEqual(rate_at_max, Decimal("300.00"))

    async def test_degenerate_bounds_fall_back_to_the_floor_rate(self) -> None:
        # An admin setting vc_min_withdrawal == vc_max_withdrawal (or an
        # inverted min > max) leaves nothing sane to interpolate across.
        async with self.sessions() as session:
            from bot.database.repositories.settings import SettingsRepository
            repo = SettingsRepository(session)
            await repo.set("vc_min_withdrawal", "10000")
            await repo.set("vc_max_withdrawal", "10000")
            rate = await _vc_rate_for_amount(session, 10_000)
        self.assertEqual(rate, Decimal("400.00"))


class RpCostRoundingTests(unittest.TestCase):
    def test_rounds_up_never_undercharges(self) -> None:
        # 10000 / 1000 = 10 exactly -- no rounding needed.
        self.assertEqual(_rp_cost_for_vc(10_000, Decimal("1000")), Decimal("10.00"))
        # 25001 / 1250 = 20.0008 -- must round UP to 20.01, not down to 20.00
        # (rounding down would hand out slightly more VC per RP than the
        # configured rate allows).
        self.assertEqual(_rp_cost_for_vc(25_001, Decimal("1250")), Decimal("20.01"))


class ParseVcAmountTests(unittest.TestCase):
    def test_plain_digits(self) -> None:
        self.assertEqual(_parse_vc_amount("15000"), 15000)

    def test_strips_spaces_and_commas(self) -> None:
        self.assertEqual(_parse_vc_amount("15 000"), 15000)
        self.assertEqual(_parse_vc_amount("15,000"), 15000)
        self.assertEqual(_parse_vc_amount("15\xa0000"), 15000)

    def test_non_numeric_returns_none(self) -> None:
        self.assertIsNone(_parse_vc_amount("abc"))
        self.assertIsNone(_parse_vc_amount("15.5"))
        self.assertIsNone(_parse_vc_amount(""))


class VcMenuTests(DbTestCase):
    async def test_menu_shows_rates_and_balance(self) -> None:
        cb = callback()
        state = FakeState()
        async with self.sessions() as session:
            await cb_withdraw_vc_menu(cb, db_user(stars_balance=Decimal("42.50")), session, state)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("42.50", rendered)
        self.assertIn("400", rendered)
        self.assertIn("800", rendered)
        self.assertIn("GRAM", rendered)

    async def test_disabled_shows_alert_not_menu(self) -> None:
        cb = callback()
        state = FakeState()
        async with self.sessions() as session:
            from bot.database.repositories.settings import SettingsRepository
            await SettingsRepository(session).set("withdraw_vc_enabled", "0")
            await cb_withdraw_vc_menu(cb, db_user(), session, state)

        cb.message.edit_text.assert_not_awaited()
        cb.answer.assert_awaited_once()
        self.assertIn("недоступен", cb.answer.await_args.args[0])


class VcAmountSelectionTests(DbTestCase):
    async def test_quick_amount_within_range_shows_confirm(self) -> None:
        cb = callback("vcwithdraw:amount:10000")
        state = FakeState()
        async with self.sessions() as session:
            await cb_vc_amount(cb, db_user(), session, state)

        self.assertEqual(state._state, VcWithdrawStates.confirm)
        self.assertEqual(state._data["vc_amount"], 10_000)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("10000 GRAM", rendered)
        self.assertIn("25.00 RP", rendered)  # 10000 / 400 = 25 (floor rate at min amount)

    async def test_amount_below_minimum_is_rejected(self) -> None:
        cb = callback("vcwithdraw:amount:custom")
        state = FakeState()
        async with self.sessions() as session:
            await cb_vc_amount(cb, db_user(), session, state)
        self.assertEqual(state._state, VcWithdrawStates.enter_amount)

        msg = message("5000")
        async with self.sessions() as session:
            await msg_vc_amount_custom(msg, db_user(), session, state)
        self.assertIn("от 10000 до 500000", msg.answer.await_args.args[0])

    async def test_amount_above_maximum_is_rejected(self) -> None:
        state = FakeState()
        msg = message("600000")
        async with self.sessions() as session:
            await msg_vc_amount_custom(msg, db_user(), session, state)
        self.assertIn("от 10000 до 500000", msg.answer.await_args.args[0])

    async def test_custom_amount_accepted_within_range(self) -> None:
        state = FakeState()
        msg = message("255000")  # exact midpoint of 10000-500000 -> rate 600
        async with self.sessions() as session:
            await msg_vc_amount_custom(msg, db_user(), session, state)
        self.assertEqual(state._data["vc_amount"], 255_000)
        self.assertIn("600", msg.answer.await_args.args[0])

    async def test_insufficient_balance_is_rejected(self) -> None:
        cb = callback("vcwithdraw:amount:100000")
        state = FakeState()
        async with self.sessions() as session:
            await cb_vc_amount(cb, db_user(stars_balance=Decimal("1")), session, state)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Недостаточно", rendered)
        self.assertIsNone(state._state)  # never advanced to confirm


class VcSubscriptionGateTests(DbTestCase):
    async def test_not_subscribed_shows_subscribe_prompt_not_finalize(self) -> None:
        cb = callback("vcwithdraw:confirm")
        state = FakeState({"vc_amount": 10_000, "vc_rate": "1000", "vc_rp_cost": "10"})
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")),
            send_message=AsyncMock(),
        )
        async with self.sessions() as session:
            session.add(db_user_row(1))
            await session.commit()
            await cb_vc_confirm(cb, db_user(), session, state, bot)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("подпишитесь", rendered.lower())
        bot.send_message.assert_not_awaited()  # never reached admin-channel notify
        # state must still be intact so "Я подписался" can retry without
        # re-entering the amount.
        self.assertEqual(state._data["vc_amount"], 10_000)

    async def test_subscribed_finalizes_immediately(self) -> None:
        cb = callback("vcwithdraw:confirm")
        state = FakeState({"vc_amount": 10_000, "vc_rate": "1000", "vc_rp_cost": "10"})
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        )
        async with self.sessions() as session:
            session.add(db_user_row(1))
            await session.commit()
            with (
                patch("bot.handlers.vc_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.vc_withdraw.settings.payments_channel_id", ""),
            ):
                await cb_vc_confirm(cb, db_user(), session, state, bot)

        bot.send_message.assert_awaited_once()
        async with self.sessions() as session:
            count = (await session.execute(select(func.count(VcWithdrawal.id)))).scalar_one()
        self.assertEqual(count, 1)

    async def test_no_channel_configured_skips_the_gate(self) -> None:
        """An empty vc_mandatory_channel setting must not block every
        withdrawal -- it means the gate is simply disabled."""
        cb = callback("vcwithdraw:confirm")
        state = FakeState({"vc_amount": 10_000, "vc_rate": "1000", "vc_rp_cost": "10"})
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(side_effect=AssertionError("must not be called")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        )
        async with self.sessions() as session:
            session.add(db_user_row(1))
            await session.commit()
            from bot.database.repositories.settings import SettingsRepository
            await SettingsRepository(session).set("vc_mandatory_channel", "")
            with (
                patch("bot.handlers.vc_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.vc_withdraw.settings.payments_channel_id", ""),
            ):
                await cb_vc_confirm(cb, db_user(), session, state, bot)

        bot.send_message.assert_awaited_once()

    async def test_check_sub_still_not_subscribed_shows_alert(self) -> None:
        cb = callback("vcwithdraw:check_sub")
        state = FakeState({"vc_amount": 10_000, "vc_rate": "1000", "vc_rp_cost": "10"})
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))
        async with self.sessions() as session:
            await cb_vc_check_sub(cb, db_user(), session, state, bot)
        cb.answer.assert_awaited_once()
        self.assertIn("не подписаны", cb.answer.await_args.args[0])

    async def test_check_sub_now_subscribed_finalizes(self) -> None:
        cb = callback("vcwithdraw:check_sub")
        state = FakeState({"vc_amount": 10_000, "vc_rate": "1000", "vc_rp_cost": "10"})
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        )
        async with self.sessions() as session:
            session.add(db_user_row(1))
            await session.commit()
            with (
                patch("bot.handlers.vc_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.vc_withdraw.settings.payments_channel_id", ""),
            ):
                await cb_vc_check_sub(cb, db_user(), session, state, bot)
        bot.send_message.assert_awaited_once()


def db_user_row(user_id: int, balance: str = "1000") -> User:
    return User(user_id=user_id, username="tester", first_name="T", stars_balance=Decimal(balance))


class VcFinalizeTests(DbTestCase):
    async def _confirmed_state(self) -> FakeState:
        return FakeState({"vc_amount": 10_000, "vc_rate": "1000", "vc_rp_cost": "10"})

    async def test_successful_withdrawal_debits_and_creates_row(self) -> None:
        cb = callback("vcwithdraw:confirm")
        state = await self._confirmed_state()
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)),
        )
        async with self.sessions() as session:
            session.add(db_user_row(10, balance="1000"))
            await session.commit()
            with (
                patch("bot.handlers.vc_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.vc_withdraw.settings.payments_channel_id", ""),
            ):
                await cb_vc_confirm(cb, db_user(user_id=10), session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 10)
            withdrawals = (await session.execute(select(VcWithdrawal))).scalars().all()

        self.assertEqual(float(saved_user.stars_balance), 990.0)  # 1000 - 10
        self.assertEqual(len(withdrawals), 1)
        self.assertEqual(float(withdrawals[0].vc_amount), 10000.0)
        self.assertEqual(withdrawals[0].status, "pending")
        self.assertEqual(withdrawals[0].admin_message_id, 42)

    async def test_admin_channel_not_configured_does_not_debit(self) -> None:
        cb = callback("vcwithdraw:confirm")
        state = await self._confirmed_state()
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
        async with self.sessions() as session:
            session.add(db_user_row(11))
            await session.commit()
            with patch("bot.handlers.vc_withdraw.settings.admin_channel_id", ""):
                await cb_vc_confirm(cb, db_user(user_id=11), session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 11)
        self.assertEqual(float(saved_user.stars_balance), 1000.0)

    async def test_admin_send_failure_refunds_in_full(self) -> None:
        cb = callback("vcwithdraw:confirm")
        state = await self._confirmed_state()
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
            send_message=AsyncMock(side_effect=RuntimeError("offline")),
        )
        async with self.sessions() as session:
            session.add(db_user_row(12))
            await session.commit()
            with (
                patch("bot.handlers.vc_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.vc_withdraw.settings.payments_channel_id", ""),
            ):
                await cb_vc_confirm(cb, db_user(user_id=12), session, state, bot)

        async with self.sessions() as session:
            saved_user = await session.get(User, 12)
            count = (await session.execute(select(func.count(VcWithdrawal.id)))).scalar_one()
        self.assertEqual(float(saved_user.stars_balance), 1000.0)  # debited then refunded
        self.assertEqual(count, 0)

    async def test_posts_to_payments_channel_when_configured_and_uses_display_number(self) -> None:
        cb = callback("vcwithdraw:confirm")
        state = await self._confirmed_state()
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
            send_message=AsyncMock(side_effect=[
                SimpleNamespace(message_id=77),  # payments channel
                SimpleNamespace(message_id=88),  # admin channel
            ]),
        )
        async with self.sessions() as session:
            session.add(db_user_row(14))
            await session.commit()
            with (
                patch("bot.handlers.vc_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.vc_withdraw.settings.payments_channel_id", "-100888"),
            ):
                await cb_vc_confirm(cb, db_user(user_id=14), session, state, bot)

        self.assertEqual(bot.send_message.await_count, 2)
        payments_call, admin_call = bot.send_message.await_args_list
        self.assertEqual(payments_call.args[0], -100888)
        self.assertEqual(admin_call.args[0], -100999)
        # Same request text goes to both channels, numbered #1 (first-ever
        # allocation in this fresh in-memory test DB).
        self.assertIn("#1", payments_call.args[1])
        self.assertIn("#1", admin_call.args[1])

        async with self.sessions() as session:
            withdrawals = (await session.execute(select(VcWithdrawal))).scalars().all()
        self.assertEqual(withdrawals[0].channel_message_id, 77)
        self.assertEqual(withdrawals[0].admin_message_id, 88)
        self.assertEqual(withdrawals[0].display_number, 1)

    async def test_admin_tightened_max_after_confirm_screen_blocks_stale_amount(self) -> None:
        """State was set with vc_amount=10000 while max was 500000; if an
        admin lowers vc_max_withdrawal below that before the user confirms,
        the stale amount must be rejected, not silently honored."""
        cb = callback("vcwithdraw:confirm")
        state = await self._confirmed_state()
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        )
        async with self.sessions() as session:
            session.add(db_user_row(13))
            await session.commit()
            from bot.database.repositories.settings import SettingsRepository
            await SettingsRepository(session).set("vc_max_withdrawal", "5000")
            with patch("bot.handlers.vc_withdraw.settings.admin_channel_id", "-100999"):
                await cb_vc_confirm(cb, db_user(user_id=13), session, state, bot)

        bot.send_message.assert_not_awaited()
        async with self.sessions() as session:
            saved_user = await session.get(User, 13)
            count = (await session.execute(select(func.count(VcWithdrawal.id)))).scalar_one()
        self.assertEqual(float(saved_user.stars_balance), 1000.0)  # never debited
        self.assertEqual(count, 0)

    async def test_stale_state_amount_missing_is_a_clean_no_op(self) -> None:
        cb = callback("vcwithdraw:confirm")
        state = FakeState()  # no vc_amount at all
        bot = SimpleNamespace(get_chat_member=AsyncMock())
        async with self.sessions() as session:
            await cb_vc_confirm(cb, db_user(), session, state, bot)
        bot.get_chat_member.assert_not_awaited()
        cb.message.edit_text.assert_not_awaited()


class VcAdminApproveRejectTests(DbTestCase):
    async def test_approve_marks_status_and_notifies_user(self) -> None:
        async with self.sessions() as session:
            session.add(db_user_row(20))
            await session.commit()
            w = await VcWithdrawalRepository(session).create(20, Decimal(10000), Decimal(1000), Decimal(10))
            await session.commit()
            wid = w.id

        cb = SimpleNamespace(
            data=f"admin:vcwithdraw_approve:{wid}",
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(edit_text=AsyncMock(), text="request"),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            with patch("bot.handlers.admin_channel.settings.admin_ids", "999"):
                await cb_vcwithdraw_approve(cb, session)

        async with self.sessions() as session:
            w = await session.get(VcWithdrawal, wid)
        self.assertEqual(w.status, "approved")
        cb.bot.send_message.assert_awaited_once()
        self.assertIn("10000", cb.bot.send_message.await_args.args[1])

    async def test_reject_marks_status_and_tells_user_not_refunded(self) -> None:
        async with self.sessions() as session:
            session.add(db_user_row(21))
            await session.commit()
            w = await VcWithdrawalRepository(session).create(21, Decimal(25000), Decimal(1250), Decimal(20))
            await session.commit()
            wid = w.id

        cb = SimpleNamespace(
            data=f"admin:vcwithdraw_reject:{wid}",
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(edit_text=AsyncMock(), text="request"),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            with patch("bot.handlers.admin_channel.settings.admin_ids", "999"):
                await cb_vcwithdraw_reject(cb, session)

        async with self.sessions() as session:
            w = await session.get(VcWithdrawal, wid)
        self.assertEqual(w.status, "rejected")
        cb.bot.send_message.assert_awaited_once()
        self.assertIn("не возвращены", cb.bot.send_message.await_args.args[1])

    async def test_non_admin_cannot_approve(self) -> None:
        async with self.sessions() as session:
            session.add(db_user_row(22))
            await session.commit()
            w = await VcWithdrawalRepository(session).create(22, Decimal(10000), Decimal(1000), Decimal(10))
            await session.commit()
            wid = w.id

        cb = SimpleNamespace(
            data=f"admin:vcwithdraw_approve:{wid}",
            from_user=SimpleNamespace(id=1),  # not an admin
            message=SimpleNamespace(edit_text=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            with patch("bot.handlers.admin_channel.settings.admin_ids", "999"):
                await cb_vcwithdraw_approve(cb, session)

        async with self.sessions() as session:
            w = await session.get(VcWithdrawal, wid)
        self.assertEqual(w.status, "pending")
        cb.bot.send_message.assert_not_awaited()

    async def test_already_processed_cannot_be_approved_again(self) -> None:
        async with self.sessions() as session:
            session.add(db_user_row(23))
            await session.commit()
            w = await VcWithdrawalRepository(session).create(23, Decimal(10000), Decimal(1000), Decimal(10))
            await session.commit()
            wid = w.id
            await VcWithdrawalRepository(session).approve(wid)

        cb = SimpleNamespace(
            data=f"admin:vcwithdraw_approve:{wid}",
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(edit_text=AsyncMock()),
            bot=SimpleNamespace(send_message=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            with patch("bot.handlers.admin_channel.settings.admin_ids", "999"):
                await cb_vcwithdraw_approve(cb, session)

        cb.answer.assert_awaited_once()
        self.assertIn("уже обработана", cb.answer.await_args.args[0])
        cb.bot.send_message.assert_not_awaited()


class CrossCurrencyNumberingTests(DbTestCase):
    async def test_stars_and_vc_withdrawals_share_one_sequence(self) -> None:
        """A Stars withdrawal followed by a VC withdrawal must NOT both come
        out as "#1" -- they draw from the same shared counter, not each
        table's own independent autoincrement id."""
        from bot.database.repositories.withdrawal import WithdrawalRepository
        from bot.services.withdrawal_numbering import next_withdrawal_number

        async with self.sessions() as session:
            stars_repo = WithdrawalRepository(session)
            stars_w = await stars_repo.create(1, 15.0, "tester", "fragment", Decimal("45"))
            stars_w.display_number = await next_withdrawal_number(session)
            await session.commit()
            stars_number = stars_w.display_number

        cb = callback("vcwithdraw:confirm")
        state = FakeState({"vc_amount": 10_000, "vc_rate": "1000", "vc_rp_cost": "10"})
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        )
        async with self.sessions() as session:
            session.add(db_user_row(15))
            await session.commit()
            with (
                patch("bot.handlers.vc_withdraw.settings.admin_channel_id", "-100999"),
                patch("bot.handlers.vc_withdraw.settings.payments_channel_id", ""),
            ):
                await cb_vc_confirm(cb, db_user(user_id=15), session, state, bot)

        async with self.sessions() as session:
            vc_w = (await session.execute(select(VcWithdrawal))).scalars().one()
        self.assertEqual(stars_number, 1)
        self.assertEqual(vc_w.display_number, 2)


class WithdrawMethodChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_top_level_menu_offers_both_stars_and_vc(self) -> None:
        from bot.handlers.withdraw import cb_withdraw_menu

        cb = callback()
        state = FakeState()
        await cb_withdraw_menu(cb, state)

        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        self.assertIn("withdraw:choose:stars", callback_datas)
        self.assertIn("withdraw:choose:vc", callback_datas)


if __name__ == "__main__":
    unittest.main()
