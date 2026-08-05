import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, ChatBonusCode, ChatMembership, ChatPromoCode, User
from bot.database.repositories.chat_bonus import ChatBonusRepository
from bot.database.repositories.chat_promo import ChatPromoRepository
from bot.handlers.group.chat_bonus import (
    cb_bonus_mode,
    cb_bonus_sponsors_done,
    msg_bonus_limit,
    msg_bonus_pick_winner,
    msg_bonus_redeem,
    msg_bonus_reward,
)
from bot.handlers.group.chat_promo import cb_chat_promo_start, msg_chat_promo_code, msg_chat_promo_redeem
from bot.keyboards.group.owner_menu import owner_menu_kb
from bot.services.chat_eligibility import eligibility_reason
from bot.states.group import ChatOwnerBonusStates, ChatOwnerPromoStates


def _fsm_state(data: dict):
    state = SimpleNamespace()
    state.get_data = AsyncMock(return_value=dict(data))
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    return state


def _callback(chat_id: int, user_id: int, data: str, first_name: str = "User"):
    message = SimpleNamespace(chat=SimpleNamespace(id=chat_id, title="Chat"), answer=AsyncMock())
    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id, first_name=first_name),
        data=data,
        answer=AsyncMock(),
    )


def _message(chat_id: int, user_id: int, text: str, first_name: str = "User", reply_to=None):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Chat"),
        from_user=SimpleNamespace(id=user_id, first_name=first_name),
        text=text,
        reply=AsyncMock(),
        answer=AsyncMock(),
        reply_to_message=reply_to,
    )


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class ChatPromoTests(ChatModelsTestCase):
    async def _setup_chat_and_member(
        self, chat_id: int, owner_id: int, member_id: int, joined_days_ago: int, message_count: int, is_vip: bool = False,
    ) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=chat_id, title="T", status="active", owner_user_id=owner_id, member_count=300))
            session.add(User(user_id=member_id, first_name="M", is_vip=is_vip))
            session.add(
                ChatMembership(
                    chat_id=chat_id,
                    user_id=member_id,
                    joined_at=datetime.utcnow() - timedelta(days=joined_days_ago),
                    message_count=message_count,
                )
            )
            await session.commit()

    async def test_owner_creates_promo_code(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=42, member_count=300))
            await session.commit()

        state = _fsm_state({"chat_id": -1})
        message = _message(-1, 42, "WELCOME")
        async with self.sessions() as session:
            await msg_chat_promo_code(message, state, session)

        message.reply.assert_awaited_once()
        async with self.sessions() as session:
            promo = await ChatPromoRepository(session).get_by_code(-1, "WELCOME")
        self.assertIsNotNone(promo)
        self.assertEqual(promo.created_by, 42)

    async def test_second_promo_creation_via_stale_state_is_blocked(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-20, title="T", status="active", owner_user_id=42, member_count=300))
            await session.commit()

        state = _fsm_state({"chat_id": -20})
        first = _message(-20, 42, "FIRST")
        async with self.sessions() as session:
            await msg_chat_promo_code(first, state, session)

        # A stale FSM state (e.g. the owner's client re-sent the same
        # "enter_code" step, or the state never got cleared) must not be
        # able to create a second promo code for the same chat.
        state2 = _fsm_state({"chat_id": -20})
        second = _message(-20, 42, "SECOND")
        async with self.sessions() as session:
            await msg_chat_promo_code(second, state2, session)

        rendered = second.reply.await_args.args[0]
        self.assertIn("уже был создан промокод", rendered)
        async with self.sessions() as session:
            repo = ChatPromoRepository(session)
            self.assertIsNotNone(await repo.get_by_code(-20, "FIRST"))
            self.assertIsNone(await repo.get_by_code(-20, "SECOND"))

    async def test_promo_start_callback_blocks_when_promo_already_exists(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-21, title="T", status="active", owner_user_id=42, member_count=300))
            await session.commit()
            await ChatPromoRepository(session).create(-21, "EXISTING", created_by=42)

        message = SimpleNamespace(chat=SimpleNamespace(id=-21))
        callback = SimpleNamespace(message=message, from_user=SimpleNamespace(id=42), answer=AsyncMock())
        state = _fsm_state({})
        async with self.sessions() as session:
            await cb_chat_promo_start(callback, session, state)

        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))
        state.set_state.assert_not_awaited()

    async def test_promo_start_callback_allows_first_creation(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-22, title="T", status="active", owner_user_id=42, member_count=300))
            await session.commit()

        message = SimpleNamespace(chat=SimpleNamespace(id=-22), answer=AsyncMock())
        callback = SimpleNamespace(message=message, from_user=SimpleNamespace(id=42), answer=AsyncMock())
        state = _fsm_state({})
        async with self.sessions() as session:
            await cb_chat_promo_start(callback, session, state)

        state.set_state.assert_awaited_once_with(ChatOwnerPromoStates.enter_code)
        message.answer.assert_awaited_once()

    async def test_eligible_member_redeems_and_gets_reward(self) -> None:
        await self._setup_chat_and_member(-2, owner_id=42, member_id=500, joined_days_ago=5, message_count=600)
        async with self.sessions() as session:
            promo = await ChatPromoRepository(session).create(-2, "GO", created_by=42)

        message = _message(-2, 500, f"промокод {promo.code}")
        async with self.sessions() as session:
            await msg_chat_promo_redeem(message, session)

        message.reply.assert_awaited_once()
        rendered = message.reply.await_args.args[0]
        self.assertIn("0.3", rendered)

        async with self.sessions() as session:
            user = await session.get(User, 500)
        self.assertEqual(user.stars_balance, Decimal("0.30"))

        # Second attempt is rejected — one-time use per user.
        message2 = _message(-2, 500, f"промокод {promo.code}")
        async with self.sessions() as session:
            await msg_chat_promo_redeem(message2, session)
        rendered2 = message2.reply.await_args.args[0]
        self.assertIn("уже использован", rendered2)

    async def test_vip_member_gets_higher_reward(self) -> None:
        await self._setup_chat_and_member(-3, owner_id=42, member_id=501, joined_days_ago=5, message_count=600, is_vip=True)
        async with self.sessions() as session:
            promo = await ChatPromoRepository(session).create(-3, "VIP1", created_by=42)

        message = _message(-3, 501, f"промокод {promo.code}")
        async with self.sessions() as session:
            await msg_chat_promo_redeem(message, session)

        async with self.sessions() as session:
            user = await session.get(User, 501)
        self.assertEqual(user.stars_balance, Decimal("0.50"))

    async def test_too_few_messages_rejected(self) -> None:
        await self._setup_chat_and_member(-4, owner_id=42, member_id=502, joined_days_ago=5, message_count=10)
        async with self.sessions() as session:
            promo = await ChatPromoRepository(session).create(-4, "NOPE", created_by=42)

        message = _message(-4, 502, f"промокод {promo.code}")
        async with self.sessions() as session:
            await msg_chat_promo_redeem(message, session)

        rendered = message.reply.await_args.args[0]
        self.assertIn("недоступен", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 502)
        self.assertEqual(user.stars_balance, Decimal("0"))

    async def test_unregistered_user_rejected(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-5, title="T", status="active", owner_user_id=42, member_count=300))
            session.add(
                ChatMembership(
                    chat_id=-5, user_id=999,
                    joined_at=datetime.utcnow() - timedelta(days=10), message_count=1000,
                )
            )
            promo_repo = ChatPromoRepository(session)
            await session.commit()
            promo = await promo_repo.create(-5, "X", created_by=42)

        message = _message(-5, 999, f"промокод {promo.code}")
        async with self.sessions() as session:
            await msg_chat_promo_redeem(message, session)
        args, kwargs = message.reply.await_args
        self.assertIn("пройдите регистрацию", args[0])
        self.assertIn("?start=group", kwargs["reply_markup"].inline_keyboard[0][0].url)


class OwnerMenuKeyboardTests(unittest.TestCase):
    def test_promo_button_shown_when_no_promo_exists(self) -> None:
        kb = owner_menu_kb(broadcast_opt_in=False, has_promo=False)
        callback_datas = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        self.assertIn("chatmenu:promo", callback_datas)

    def test_promo_button_hidden_once_promo_exists(self) -> None:
        kb = owner_menu_kb(broadcast_opt_in=False, has_promo=True)
        callback_datas = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        self.assertNotIn("chatmenu:promo", callback_datas)
        self.assertIn("chatmenu:bonus", callback_datas)


class EligibilityHelperTests(ChatModelsTestCase):
    async def test_reasons_reported_in_priority_order(self) -> None:
        async with self.sessions() as session:
            reason = await eligibility_reason(session, -1, 1, min_days=2, min_messages=500)
        self.assertIn("зарегистрированным", reason)


class ChatBonusTests(ChatModelsTestCase):
    async def test_self_serve_bonus_full_flow_charges_owner_with_commission(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-10, title="T", status="active", owner_user_id=42, member_count=300))
            session.add(User(user_id=42, first_name="Owner", stars_balance=Decimal("100")))
            session.add(User(user_id=700, first_name="Member"))
            session.add(
                ChatMembership(
                    chat_id=-10, user_id=700,
                    joined_at=datetime.utcnow() - timedelta(days=10), message_count=1000,
                )
            )
            await session.commit()

        state = _fsm_state({"chat_id": -10, "code": "GIVE10"})
        message = _message(-10, 42, "0.5")
        async with self.sessions() as session:
            await msg_bonus_reward(message, state)
        state.update_data.assert_any_call(reward="0.5")

        state2 = _fsm_state({"chat_id": -10, "code": "GIVE10", "reward": "0.5"})
        message2 = _message(-10, 42, "10")
        async with self.sessions() as session:
            await msg_bonus_limit(message2, state2)
        state2.set_state.assert_awaited_with(ChatOwnerBonusStates.choose_mode)

        state3 = _fsm_state({
            "chat_id": -10, "code": "GIVE10", "reward": "0.5", "limit": 10, "mode": "self_serve", "sponsors": [],
        })
        cb3 = _callback(-10, 42, "chatbonus:sponsors:done")
        async with self.sessions() as session:
            await cb_bonus_sponsors_done(cb3, state3, session)

        # 0.5 * 10 = 5, +7% commission = 5.35
        async with self.sessions() as session:
            owner = await session.get(User, 42)
            bonus = await ChatBonusRepository(session).get_by_code(-10, "GIVE10")
        self.assertEqual(owner.stars_balance, Decimal("94.65"))
        self.assertEqual(bonus.total_charged, Decimal("5.35"))
        self.assertEqual(bonus.usage_limit, 10)

        redeem_msg = _message(-10, 700, "бонус GIVE10")
        async with self.sessions() as session:
            await msg_bonus_redeem(redeem_msg, session, AsyncMock())
        redeem_msg.reply.assert_awaited_once()
        async with self.sessions() as session:
            member = await session.get(User, 700)
            bonus_after = await ChatBonusRepository(session).get_by_code(-10, "GIVE10")
        self.assertEqual(member.stars_balance, Decimal("0.50"))
        self.assertEqual(bonus_after.used_count, 1)

    async def test_bonus_creation_fails_if_owner_balance_too_low(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-11, title="T", status="active", owner_user_id=42, member_count=300))
            session.add(User(user_id=42, first_name="Owner", stars_balance=Decimal("1")))
            await session.commit()

        state = _fsm_state({
            "chat_id": -11, "code": "TOOEXPENSIVE", "reward": "1", "limit": 10, "mode": "self_serve", "sponsors": [],
        })
        cb = _callback(-11, 42, "chatbonus:sponsors:done")
        async with self.sessions() as session:
            await cb_bonus_sponsors_done(cb, state, session)

        rendered = cb.message.answer.await_args.args[0]
        self.assertIn("Недостаточно", rendered)
        async with self.sessions() as session:
            owner = await session.get(User, 42)
            bonus = await ChatBonusRepository(session).get_by_code(-11, "TOOEXPENSIVE")
        self.assertEqual(owner.stars_balance, Decimal("1"))
        self.assertIsNone(bonus)

    async def test_contest_winner_picked_via_reply(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-12, title="T", status="active", owner_user_id=42, member_count=300))
            session.add(User(user_id=42, first_name="Owner", stars_balance=Decimal("100")))
            session.add(User(user_id=800, first_name="Winner"))
            await session.commit()
            bonus = await ChatBonusRepository(session).create(
                chat_id=-12,
                code="CONTEST1",
                reward_amount=Decimal("2"),
                usage_limit=1,
                commission_rate=Decimal("0.07"),
                mode="contest",
                min_days_in_chat=0,
                min_messages=0,
                condition_note=None,
                created_by=42,
            )

        winner_msg = SimpleNamespace(from_user=SimpleNamespace(id=800, first_name="Winner"))
        pick_msg = _message(-12, 42, f"выбрать {bonus.code}", reply_to=winner_msg)
        async with self.sessions() as session:
            await msg_bonus_pick_winner(pick_msg, session)

        pick_msg.reply.assert_awaited_once()
        rendered = pick_msg.reply.await_args.args[0]
        self.assertIn("Победитель", rendered)
        async with self.sessions() as session:
            winner = await session.get(User, 800)
        self.assertEqual(winner.stars_balance, Decimal("2"))

        # A non-owner can't pick a winner.
        pick_msg2 = _message(-12, 999, f"выбрать {bonus.code}", reply_to=winner_msg)
        async with self.sessions() as session:
            await msg_bonus_pick_winner(pick_msg2, session)
        pick_msg2.reply.assert_not_awaited()

    async def test_contest_mode_is_a_stub_no_fsm_no_bonus_created(self) -> None:
        state = _fsm_state({"chat_id": -14, "code": "CONTESTCODE", "reward": "1", "limit": 5})
        message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(message=message, data="chatbonus:mode:contest", answer=AsyncMock())

        await cb_bonus_mode(callback, state)

        message.answer.assert_awaited_once_with("🚧 Этот раздел пока находится в разработке.")
        callback.answer.assert_awaited_once()
        # No FSM transition and no data written — the choose_mode prompt
        # (with both mode buttons) is left exactly as it was.
        state.set_state.assert_not_awaited()
        state.update_data.assert_not_awaited()

        async with self.sessions() as session:
            bonus = await ChatBonusRepository(session).get_by_code(-14, "CONTESTCODE")
        self.assertIsNone(bonus)

    async def test_self_serve_mode_still_advances_fsm_normally(self) -> None:
        state = _fsm_state({"chat_id": -14, "code": "SELFCODE", "reward": "1", "limit": 5})
        message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(message=message, data="chatbonus:mode:self_serve", answer=AsyncMock())

        await cb_bonus_mode(callback, state)

        state.update_data.assert_awaited_once_with(mode="self_serve", sponsors=[])
        state.set_state.assert_awaited_once_with(ChatOwnerBonusStates.choose_sponsors)
        message.answer.assert_awaited_once()
        self.assertNotIn("разработке", message.answer.await_args.args[0])

    async def test_self_serve_code_cannot_be_redeemed_via_pick_winner_path(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-13, title="T", status="active", owner_user_id=42))
            await session.commit()
            await ChatBonusRepository(session).create(
                chat_id=-13, code="SELFONLY", reward_amount=Decimal("1"), usage_limit=5,
                commission_rate=Decimal("0.07"), mode="self_serve",
                min_days_in_chat=0, min_messages=0, condition_note=None, created_by=42,
            )

        winner_msg = SimpleNamespace(from_user=SimpleNamespace(id=800, first_name="Winner"))
        pick_msg = _message(-13, 42, "выбрать SELFONLY", reply_to=winner_msg)
        async with self.sessions() as session:
            await msg_bonus_pick_winner(pick_msg, session)
        rendered = pick_msg.reply.await_args.args[0]
        self.assertIn("не найден", rendered)


if __name__ == "__main__":
    unittest.main()
