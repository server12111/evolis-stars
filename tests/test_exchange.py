import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import RpPurchase, User
from bot.handlers.exchange import (
    cb_exchange_amount,
    cb_exchange_confirm,
    cb_exchange_custom,
    msg_exchange_custom_amount,
    msg_rp_purchase_paid,
    process_rp_purchase_pre_checkout,
)
from bot.states.exchange import ExchangeStates


def _callback(data: str, user_id: int = 1):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, data=data, from_user=SimpleNamespace(id=user_id), answer=AsyncMock())


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _add_user(self, user_id: int, **kwargs) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=user_id, first_name="U", **kwargs))
            await session.commit()


class AmountAndInvoiceTests(ChatModelsTestCase):
    async def test_amount_screen_shows_rate_applied_rp_total(self) -> None:
        cb = _callback("exchange:amount:50")
        async with self.sessions() as session:
            with patch("bot.handlers.exchange.SettingsRepository.get_float", AsyncMock(return_value=2.0)):
                await cb_exchange_amount(cb, session)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("50 ⭐", rendered)
        self.assertIn("100.00 RP⭐️", rendered)

    async def test_confirm_sends_invoice_with_rate_locked_into_payload(self) -> None:
        cb = _callback("exchange:confirm:50", user_id=42)
        bot = AsyncMock()
        async with self.sessions() as session:
            with patch("bot.handlers.exchange.SettingsRepository.get_float", AsyncMock(return_value=1.5)):
                await cb_exchange_confirm(cb, session, bot)

        bot.send_invoice.assert_awaited_once()
        kwargs = bot.send_invoice.await_args.kwargs
        self.assertEqual(kwargs["currency"], "XTR")
        self.assertEqual(kwargs["payload"], "rp_buy:42:75.00:1.5")
        self.assertEqual(kwargs["prices"][0].amount, 50)

    async def test_invalid_amount_rejected(self) -> None:
        cb = _callback("exchange:amount:999")
        async with self.sessions() as session:
            await cb_exchange_amount(cb, session)
        cb.answer.assert_awaited_once_with("❌ Неверная сумма.", show_alert=True)
        cb.message.edit_text.assert_not_awaited()


class CustomAmountTests(ChatModelsTestCase):
    async def test_custom_button_asks_for_amount(self) -> None:
        cb = _callback("exchange:custom")
        state = SimpleNamespace(set_state=AsyncMock())
        await cb_exchange_custom(cb, state)
        state.set_state.assert_awaited_once_with(ExchangeStates.enter_amount)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Stars", rendered)

    async def test_typed_amount_shows_confirm_screen_not_limited_to_presets(self) -> None:
        message = SimpleNamespace(text="777", answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        async with self.sessions() as session:
            with patch("bot.handlers.exchange.SettingsRepository.get_float", AsyncMock(return_value=1.0)):
                await msg_exchange_custom_amount(message, session, state)
        state.clear.assert_awaited_once()
        rendered = message.answer.await_args.args[0]
        self.assertIn("777 ⭐", rendered)
        self.assertIn("777.00 RP⭐️", rendered)

    async def test_non_numeric_input_rejected_with_retry_prompt(self) -> None:
        message = SimpleNamespace(text="abc", answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        async with self.sessions() as session:
            await msg_exchange_custom_amount(message, session, state)
        state.clear.assert_not_awaited()
        message.answer.assert_awaited_once()

    async def test_out_of_range_amount_rejected(self) -> None:
        message = SimpleNamespace(text="999999999", answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        async with self.sessions() as session:
            await msg_exchange_custom_amount(message, session, state)
        state.clear.assert_not_awaited()
        rendered = message.answer.await_args.args[0]
        self.assertIn("от", rendered)

    async def test_typed_amount_can_actually_be_purchased_via_confirm(self) -> None:
        cb = _callback("exchange:confirm:777", user_id=5)
        bot = AsyncMock()
        async with self.sessions() as session:
            with patch("bot.handlers.exchange.SettingsRepository.get_float", AsyncMock(return_value=1.0)):
                await cb_exchange_confirm(cb, session, bot)
        bot.send_invoice.assert_awaited_once()
        self.assertEqual(bot.send_invoice.await_args.kwargs["prices"][0].amount, 777)


class PreCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_rp_buy_payload(self) -> None:
        pcq = SimpleNamespace(invoice_payload="rp_buy:1:50:1", answer=AsyncMock())
        await process_rp_purchase_pre_checkout(pcq)
        pcq.answer.assert_awaited_once_with(ok=True)


class SuccessfulPaymentTests(ChatModelsTestCase):
    async def test_payment_credits_rp_and_records_purchase(self) -> None:
        await self._add_user(7, stars_balance=Decimal("10"))
        message = SimpleNamespace(
            successful_payment=SimpleNamespace(
                invoice_payload="rp_buy:7:75.00:1.5",
                total_amount=50,
                telegram_payment_charge_id="charge_abc",
            ),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            db_user = await session.get(User, 7)
            await msg_rp_purchase_paid(message, db_user, session)

        async with self.sessions() as session:
            saved = await session.get(User, 7)
            purchase = (await session.execute(select(RpPurchase))).scalars().one()
        self.assertEqual(saved.stars_balance, Decimal("85.00"))
        self.assertEqual(purchase.rp_credited, Decimal("75.00"))
        self.assertEqual(purchase.stars_paid, 50)
        self.assertEqual(purchase.telegram_payment_charge_id, "charge_abc")

    async def test_duplicate_charge_id_does_not_credit_twice(self) -> None:
        """Simulates Telegram redelivering the same successful_payment
        update — the unique constraint on telegram_payment_charge_id must
        prevent a second credit."""
        await self._add_user(8, stars_balance=Decimal("0"))
        message = SimpleNamespace(
            successful_payment=SimpleNamespace(
                invoice_payload="rp_buy:8:10.00:1",
                total_amount=10,
                telegram_payment_charge_id="dup_charge",
            ),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            db_user = await session.get(User, 8)
            await msg_rp_purchase_paid(message, db_user, session)

        async with self.sessions() as session:
            db_user = await session.get(User, 8)
            await msg_rp_purchase_paid(message, db_user, session)

        async with self.sessions() as session:
            saved = await session.get(User, 8)
            count = len((await session.execute(select(RpPurchase))).scalars().all())
        self.assertEqual(saved.stars_balance, Decimal("10.00"))  # credited once, not twice
        self.assertEqual(count, 1)

    async def test_payload_user_mismatch_is_ignored(self) -> None:
        await self._add_user(9, stars_balance=Decimal("0"))
        message = SimpleNamespace(
            successful_payment=SimpleNamespace(
                invoice_payload="rp_buy:999999:10.00:1",
                total_amount=10,
                telegram_payment_charge_id="charge_mismatch",
            ),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            db_user = await session.get(User, 9)
            await msg_rp_purchase_paid(message, db_user, session)
        async with self.sessions() as session:
            saved = await session.get(User, 9)
        self.assertEqual(saved.stars_balance, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
