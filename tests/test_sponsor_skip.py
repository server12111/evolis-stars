import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.start import (
    cb_sponsor_skip,
    cb_sponsor_skip_confirm,
    msg_sponsor_skip_paid,
    process_sponsor_skip_pre_checkout,
)
from bot.middlewares.sponsor_wall import SponsorWallMiddleware
from bot.services.sponsor_waves import skip_current_wave


def _wave_items(prefix: str, count: int) -> list[dict]:
    return [{"provider": "tgrass", "url": f"https://t.me/{prefix}{i}", "name": f"Ch{i}"} for i in range(count)]


def _callback(data: str, user_id: int = 1):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    from_user = SimpleNamespace(id=user_id, is_premium=False, username="u", language_code="ru")
    return SimpleNamespace(message=message, data=data, from_user=from_user, answer=AsyncMock())


class SkipCurrentWaveUnitTests(unittest.TestCase):
    def test_no_second_wave_completes_immediately(self) -> None:
        user = SimpleNamespace(sponsor_wave=1, sponsor_wave_one=json.dumps(_wave_items("a", 3)), sponsor_wave_two=None)
        state = skip_current_wave(user)
        self.assertEqual(state.status, "complete")
        self.assertEqual(user.sponsor_wave, 3)

    def test_second_wave_advances_to_it_instead_of_completing(self) -> None:
        user = SimpleNamespace(
            sponsor_wave=1,
            sponsor_wave_one=json.dumps(_wave_items("a", 3)),
            sponsor_wave_two=json.dumps(_wave_items("b", 2)),
        )
        state = skip_current_wave(user)
        self.assertEqual(state.status, "pending")
        self.assertEqual(user.sponsor_wave, 2)
        self.assertEqual(len(state.items or []), 2)


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


def _fake_bot(confirmed_subscribed: set[str] | None = None) -> SimpleNamespace:
    # get_chat_member raising means _drop_confirmed_subscriptions /
    # _reinstate_expired_pinned_sponsors can't independently verify
    # anything for that chat — an unverifiable sponsor is kept pending
    # rather than dropped (a provider simply omitting it is NOT proof of
    # subscription). `confirmed_subscribed` lets a test explicitly mark
    # specific @usernames as genuinely, positively subscribed instead.
    confirmed = confirmed_subscribed or set()

    async def get_chat_member(chat_id, user_id):
        if chat_id in confirmed:
            return SimpleNamespace(status="member")
        raise Exception("no live check in this test")

    return SimpleNamespace(
        get_chat_member=AsyncMock(side_effect=get_chat_member),
        send_invoice=AsyncMock(),
    )


class SponsorSkipButtonTests(ChatModelsTestCase):
    async def test_shows_confirmation_with_price(self) -> None:
        items = _wave_items("a", 3)
        await self._add_user(
            10, sponsor_wave=1, sponsor_wave_one=json.dumps(items), sponsor_wave_two=None,
        )
        cb = _callback("sponsor_skip", user_id=10)
        bot = _fake_bot()
        async with self.sessions() as session:
            db_user = await session.get(User, 10)
            with (
                patch("bot.handlers.start.settings.tgrass_code", "cfg"),
                patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=items)),
                patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            ):
                await cb_sponsor_skip(cb, db_user, session, bot)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("3", rendered)
        self.assertIn("3⭐", rendered)

    async def test_skip_price_does_not_grow_or_persist_the_wave(self) -> None:
        """Bug found in review: opening the skip dialog runs the same
        evaluate_waves() that a real check does, which can top up a
        shrunk wave with fresh replacements and commit them. Merely
        viewing the price must not silently inflate what the user owes
        or grow their saved wave, even when a much larger provider pool
        is available to top up from."""
        items = _wave_items("a", 5)
        await self._add_user(
            14, sponsor_wave=1, sponsor_wave_one=json.dumps(items), sponsor_wave_two=None,
        )
        cb = _callback("sponsor_skip", user_id=14)
        bot = _fake_bot(confirmed_subscribed={f"@a{i}" for i in range(2, 5)})
        # Only 2 of the original 5 are still pending, but tgrass also
        # offers a much larger fresh pool that a top-up (if it ran here)
        # would happily pull replacements from.
        still_pending = items[:2] + _wave_items("new", 20)
        async with self.sessions() as session:
            db_user = await session.get(User, 14)
            with (
                patch("bot.handlers.start.settings.tgrass_code", "cfg"),
                patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=still_pending)),
                patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            ):
                await cb_sponsor_skip(cb, db_user, session, bot)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("2 спонсор", rendered)
        self.assertNotIn("22", rendered)

        async with self.sessions() as session:
            saved_user = await session.get(User, 14)
        self.assertEqual(len(json.loads(saved_user.sponsor_wave_one)), 5)

    async def test_price_reflects_only_still_unsubscribed_not_the_full_frozen_wave(self) -> None:
        """Regression: the frozen wave can be larger than what's still
        actually unsubscribed (e.g. the user already subscribed to some of
        it since the wave was shown) — the skip price must match reality,
        not the original wave size."""
        items = _wave_items("a", 5)
        await self._add_user(
            13, sponsor_wave=1, sponsor_wave_one=json.dumps(items), sponsor_wave_two=None,
        )
        cb = _callback("sponsor_skip", user_id=13)
        # The user has since subscribed to all but 2 — check_tgrass only
        # reports those 2 as still unsubscribed, and the bot positively
        # confirms membership for the other 3 (not just their absence from
        # tgrass's report, which alone must NOT be read as "subscribed").
        bot = _fake_bot(confirmed_subscribed={f"@a{i}" for i in range(2, 5)})
        still_pending = items[:2]
        async with self.sessions() as session:
            db_user = await session.get(User, 13)
            with (
                patch("bot.handlers.start.settings.tgrass_code", "cfg"),
                patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=still_pending)),
                patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            ):
                await cb_sponsor_skip(cb, db_user, session, bot)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("2 спонсор", rendered)
        self.assertNotIn("5 спонсор", rendered)

    async def test_nothing_to_skip_shows_alert_not_confirmation(self) -> None:
        await self._add_user(11, sponsor_wave=3, sponsor_wave_one=None, sponsor_wave_two=None)
        cb = _callback("sponsor_skip", user_id=11)
        bot = _fake_bot()
        async with self.sessions() as session:
            db_user = await session.get(User, 11)
            with (
                patch("bot.handlers.start.settings.tgrass_code", "cfg"),
                patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
                patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            ):
                await cb_sponsor_skip(cb, db_user, session, bot)

        cb.answer.assert_awaited_once()
        self.assertIn("нечего", cb.answer.await_args.args[0])
        cb.message.edit_text.assert_not_awaited()


class SponsorSkipConfirmTests(ChatModelsTestCase):
    async def test_sends_a_real_stars_invoice_for_the_right_amount(self) -> None:
        items = _wave_items("a", 4)
        await self._add_user(
            12, sponsor_wave=1, sponsor_wave_one=json.dumps(items), sponsor_wave_two=None,
        )
        cb = _callback("sponsor_skip_confirm", user_id=12)
        bot = _fake_bot()
        async with self.sessions() as session:
            db_user = await session.get(User, 12)
            with (
                patch("bot.handlers.start.settings.tgrass_code", "cfg"),
                patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=items)),
                patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            ):
                await cb_sponsor_skip_confirm(cb, db_user, session, bot)

        bot.send_invoice.assert_awaited_once()
        kwargs = bot.send_invoice.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 12)
        self.assertEqual(kwargs["currency"], "XTR")
        self.assertEqual(kwargs["payload"], "sponsor_skip:12")
        self.assertEqual(kwargs["prices"][0].amount, 4)


class PreCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_known_payload(self) -> None:
        pcq = SimpleNamespace(invoice_payload="sponsor_skip:5", answer=AsyncMock())
        await process_sponsor_skip_pre_checkout(pcq)
        pcq.answer.assert_awaited_once_with(ok=True)

    # An unrelated payload never reaches this handler at all in the real
    # router — it's routed there by the pre_checkout_query filter
    # (invoice_payload.startswith("sponsor_skip:")), not by an internal
    # check, so a different payment flow's payload can't be rejected by
    # this handler answering ok=False for it.


class SuccessfulPaymentTests(ChatModelsTestCase):
    async def test_payment_completes_wave_and_shows_main_menu(self) -> None:
        await self._add_user(
            13, sponsor_wave=1, sponsor_wave_one=json.dumps(_wave_items("a", 2)), sponsor_wave_two=None,
            sponsors_verified=False, referrer_id=None,
        )
        message = SimpleNamespace(
            successful_payment=SimpleNamespace(invoice_payload="sponsor_skip:13"),
            answer=AsyncMock(),
            answer_photo=AsyncMock(),
        )
        bot = AsyncMock()
        async with self.sessions() as session:
            db_user = await session.get(User, 13)
            await msg_sponsor_skip_paid(message, db_user, session, bot)

        async with self.sessions() as session:
            saved = await session.get(User, 13)
        self.assertTrue(saved.sponsors_verified)
        self.assertEqual(saved.sponsor_wave, 3)
        message.answer.assert_any_call("✅ Спонсоры пропущены, спасибо за оплату!")

    async def test_stale_invoice_paid_after_already_complete_is_refunded(self) -> None:
        """The user already passed (e.g. subscribed for real, or a first
        duplicate payment already completed this) by the time a second/
        stale invoice gets paid — must refund instead of silently keeping
        the Stars for nothing."""
        await self._add_user(
            16, sponsor_wave=3, sponsor_wave_one=json.dumps(_wave_items("a", 2)), sponsor_wave_two=None,
            sponsors_verified=True, referrer_id=None,
        )
        message = SimpleNamespace(
            successful_payment=SimpleNamespace(
                invoice_payload="sponsor_skip:16", telegram_payment_charge_id="charge123",
            ),
            answer=AsyncMock(),
        )
        bot = AsyncMock()
        async with self.sessions() as session:
            db_user = await session.get(User, 16)
            await msg_sponsor_skip_paid(message, db_user, session, bot)

        bot.refund_star_payment.assert_awaited_once_with(16, "charge123")
        message.answer.assert_awaited_once()
        self.assertIn("возвращена", message.answer.await_args.args[0])

    async def test_payload_for_a_different_user_is_ignored(self) -> None:
        await self._add_user(
            14, sponsor_wave=1, sponsor_wave_one=json.dumps(_wave_items("a", 2)), sponsor_wave_two=None,
            sponsors_verified=False, referrer_id=None,
        )
        message = SimpleNamespace(
            successful_payment=SimpleNamespace(invoice_payload="sponsor_skip:999999"),
            answer=AsyncMock(),
        )
        bot = AsyncMock()
        async with self.sessions() as session:
            db_user = await session.get(User, 14)
            await msg_sponsor_skip_paid(message, db_user, session, bot)

        async with self.sessions() as session:
            saved = await session.get(User, 14)
        self.assertFalse(saved.sponsors_verified)
        self.assertEqual(saved.sponsor_wave, 1)
        message.answer.assert_not_awaited()

    # An unrelated payload never reaches this handler at all in the real
    # router — it's routed there by the message filter (invoice_payload
    # prefix), not by an internal check (see msg_sponsor_skip_paid's
    # docstring comment for why an internal-only check would silently
    # swallow other payment flows' updates).


class SponsorWallMiddlewareBypassTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_payment_message_is_never_blocked_by_the_wall(self) -> None:
        """If the wall middleware intercepted the payment-confirmation
        message instead of letting it reach msg_sponsor_skip_paid, the user
        would pay real Stars and just get shown the sponsor wall again —
        this must never happen."""
        from datetime import datetime

        from aiogram.types import Chat as TgChat
        from aiogram.types import Message
        from aiogram.types import SuccessfulPayment
        from aiogram.types import Update
        from aiogram.types import User as TgUser

        db_user = SimpleNamespace(
            user_id=1, is_admin=False, sponsors_verified=False,
        )
        update = Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime.utcnow(),
                chat=TgChat(id=1, type="private"),
                from_user=TgUser(id=1, is_bot=False, first_name="A"),
                successful_payment=SuccessfulPayment(
                    currency="XTR",
                    total_amount=3,
                    invoice_payload="sponsor_skip:1",
                    telegram_payment_charge_id="tg_charge",
                    provider_payment_charge_id="",
                ),
            ),
        )
        handler = AsyncMock(return_value="handled")
        data = {"db_user": db_user, "session": SimpleNamespace(), "state": SimpleNamespace()}

        result = await SponsorWallMiddleware()(handler, update, data)

        self.assertEqual(result, "handled")
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
