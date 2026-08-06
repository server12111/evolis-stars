import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, ChatBonusCode, ChatBonusSponsor, ChatMembership, User
from bot.database.repositories.chat_bonus import (
    ChatBonusRepository,
    ChatBonusSponsorRepository,
)
from bot.handlers.group.chat_bonus import (
    cb_bonus_add_sponsor_start,
    cb_bonus_check_sponsors,
    cb_bonus_sponsors_done,
    msg_bonus_redeem,
    try_link_pending_sponsor,
    _origin_state,
    _pending_sponsor_state,
)
from bot.states.group import PendingSponsorAddStates


def _fsm_state(data: dict):
    state = SimpleNamespace()
    state.storage = MemoryStorage()
    state.get_data = AsyncMock(return_value=dict(data))
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    return state


def _callback(chat_id: int, user_id: int, data: str):
    message = SimpleNamespace(chat=SimpleNamespace(id=chat_id, title="Chat"), answer=AsyncMock())
    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id, first_name="U"),
        data=data,
        answer=AsyncMock(),
    )


def _message(chat_id: int, user_id: int, text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Chat"),
        from_user=SimpleNamespace(id=user_id, first_name="U"),
        text=text,
        reply=AsyncMock(),
        answer=AsyncMock(),
    )


def _bot():
    return AsyncMock()


def _my_chat_member(chat_id: int, chat_type: str, from_user_id: int, status: str, title: str = "Sponsor", username: str | None = None):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=chat_type, title=title, username=username),
        from_user=SimpleNamespace(id=from_user_id),
        new_chat_member=SimpleNamespace(status=status),
        old_chat_member=SimpleNamespace(status="left"),
    )


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _make_bonus(self, chat_id: int, owner_id: int, **overrides) -> ChatBonusCode:
        defaults = dict(
            chat_id=chat_id, code="CODE1", reward_amount=Decimal("1"), usage_limit=5,
            commission_rate=Decimal("0.07"), mode="self_serve", min_days_in_chat=0,
            min_messages=0, condition_note=None, created_by=owner_id,
        )
        defaults.update(overrides)
        async with self.sessions() as session:
            return await ChatBonusRepository(session).create(**defaults)


class RedeemWhitespaceAndRegistrationTests(ChatModelsTestCase):
    async def test_whitespace_around_command_still_matches(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            session.add(User(user_id=5, first_name="U", stars_balance=Decimal("0")))
            session.add(ChatMembership(chat_id=-1, user_id=5))
            await session.commit()
        await self._make_bonus(-1, 1, code="WS")

        message = _message(-1, 5, "   бонус   WS   ")
        async with self.sessions() as session:
            await msg_bonus_redeem(message, session, _bot())

        message.reply.assert_awaited_once()
        rendered = message.reply.await_args.args[0]
        self.assertIn("получен", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 5)
        self.assertEqual(user.stars_balance, Decimal("1"))

    async def test_unregistered_user_gets_registration_prompt(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-2, title="T", status="active", owner_user_id=1))
            await session.commit()
        await self._make_bonus(-2, 1, code="REG")

        message = _message(-2, 999, "бонус REG")
        async with self.sessions() as session:
            await msg_bonus_redeem(message, session, _bot())

        args, kwargs = message.reply.await_args
        self.assertIn("пройдите регистрацию", args[0])
        self.assertIn("?start=group", kwargs["reply_markup"].inline_keyboard[0][0].url)

    async def test_cannot_activate_twice(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-3, title="T", status="active", owner_user_id=1))
            session.add(User(user_id=6, first_name="U", stars_balance=Decimal("0")))
            session.add(ChatMembership(chat_id=-3, user_id=6))
            await session.commit()
        await self._make_bonus(-3, 1, code="ONCE", usage_limit=10)

        for _ in range(2):
            message = _message(-3, 6, "бонус ONCE")
            async with self.sessions() as session:
                await msg_bonus_redeem(message, session, _bot())

        async with self.sessions() as session:
            user = await session.get(User, 6)
        self.assertEqual(user.stars_balance, Decimal("1"))  # only credited once


class AddSponsorFlowTests(ChatModelsTestCase):
    async def test_add_sponsor_blocked_at_cap(self) -> None:
        state = _fsm_state({"chat_id": -1, "sponsors": [
            {"chat_id": -100, "type": "channel", "title": "A", "username": "a"},
            {"chat_id": -101, "type": "channel", "title": "B", "username": "b"},
            {"chat_id": -102, "type": "channel", "title": "C", "username": "c"},
        ]})
        cb = _callback(-1, 42, "chatbonus:addsponsor:channel")
        await cb_bonus_add_sponsor_start(cb, state, _bot())
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        cb.message.answer.assert_not_awaited()

    async def test_add_sponsor_sets_pending_marker_and_shows_deeplink(self) -> None:
        state = _fsm_state({"chat_id": -1, "sponsors": []})
        bot = SimpleNamespace(id=777)
        cb = _callback(-1, 42, "chatbonus:addsponsor:channel")
        await cb_bonus_add_sponsor_start(cb, state, bot)

        cb.message.answer.assert_awaited_once()
        pending = _pending_sponsor_state(state.storage, bot.id, 42)
        self.assertEqual(await pending.get_state(), PendingSponsorAddStates.awaiting_add)
        data = await pending.get_data()
        self.assertEqual(data["sponsor_type"], "channel")
        self.assertEqual(data["origin_chat_id"], -1)

    async def test_second_add_sponsor_click_from_different_chat_is_blocked(self) -> None:
        """The pending-sponsor slot is global per user, not per bonus flow
        — a second "Add sponsor" click (even from a completely different
        chat's bonus) while the first is still unresolved must be
        blocked, not silently overwrite origin_chat_id and misattribute
        the eventual promotion event to the wrong bonus."""
        bot = SimpleNamespace(id=777)
        storage = MemoryStorage()

        state_x = _fsm_state({"chat_id": -1, "sponsors": []})
        state_x.storage = storage
        cb_x = _callback(-1, 42, "chatbonus:addsponsor:channel")
        await cb_bonus_add_sponsor_start(cb_x, state_x, bot)

        state_y = _fsm_state({"chat_id": -2, "sponsors": []})
        state_y.storage = storage
        cb_y = _callback(-2, 42, "chatbonus:addsponsor:channel")
        await cb_bonus_add_sponsor_start(cb_y, state_y, bot)

        cb_y.answer.assert_awaited_once()
        self.assertTrue(cb_y.answer.await_args.kwargs.get("show_alert"))
        cb_y.message.answer.assert_not_awaited()

        # The original pending entry (chat X) must be untouched.
        pending = _pending_sponsor_state(storage, bot.id, 42)
        data = await pending.get_data()
        self.assertEqual(data["origin_chat_id"], -1)

    async def test_second_click_allowed_after_the_first_goes_stale(self) -> None:
        bot = SimpleNamespace(id=777)
        storage = MemoryStorage()

        state_x = _fsm_state({"chat_id": -1, "sponsors": []})
        state_x.storage = storage
        cb_x = _callback(-1, 42, "chatbonus:addsponsor:channel")
        await cb_bonus_add_sponsor_start(cb_x, state_x, bot)

        pending = _pending_sponsor_state(storage, bot.id, 42)
        stale_data = await pending.get_data()
        stale_data["armed_at"] -= 700  # older than the 600s timeout
        await pending.set_data(stale_data)

        state_y = _fsm_state({"chat_id": -2, "sponsors": []})
        state_y.storage = storage
        cb_y = _callback(-2, 42, "chatbonus:addsponsor:channel")
        await cb_bonus_add_sponsor_start(cb_y, state_y, bot)

        cb_y.message.answer.assert_awaited_once()  # not blocked this time
        data = await pending.get_data()
        self.assertEqual(data["origin_chat_id"], -2)  # overwritten as before


class TryLinkPendingSponsorTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_pending(self, storage, bot_id, user_id, sponsor_type, origin_chat_id):
        pending = _pending_sponsor_state(storage, bot_id, user_id)
        await pending.set_state(PendingSponsorAddStates.awaiting_add)
        await pending.update_data(sponsor_type=sponsor_type, origin_chat_id=origin_chat_id)

    async def test_valid_channel_gets_linked(self) -> None:
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        await self._seed_pending(storage, 1, 42, "channel", -50)

        event = _my_chat_member(-900, "channel", 42, "administrator", title="Sponsor Ch", username="sponsorch")
        handled = await try_link_pending_sponsor(bot, storage, event)

        self.assertTrue(handled)
        origin = _origin_state(storage, 1, -50, 42)
        data = await origin.get_data()
        self.assertEqual(len(data["sponsors"]), 1)
        self.assertEqual(data["sponsors"][0]["chat_id"], -900)
        pending = _pending_sponsor_state(storage, 1, 42)
        self.assertIsNone(await pending.get_state())
        # Channels don't get a confirmation post inside them (too
        # intrusive/public) — only the bonus-creation chat is notified.
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.args[0], -50)

    async def test_valid_chat_gets_linked_and_notified_in_place(self) -> None:
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        await self._seed_pending(storage, 1, 42, "chat", -50)

        event = _my_chat_member(-901, "supergroup", 42, "administrator", title="Sponsor Group", username=None)
        handled = await try_link_pending_sponsor(bot, storage, event)

        self.assertTrue(handled)
        # A regular chat/group DOES get a confirmation posted inside it,
        # plus the bonus-creation chat is notified — two messages total.
        self.assertEqual(bot.send_message.await_count, 2)
        notified_chat_ids = {call.args[0] for call in bot.send_message.await_args_list}
        self.assertEqual(notified_chat_ids, {-901, -50})

    async def test_type_mismatch_rejected(self) -> None:
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        await self._seed_pending(storage, 1, 42, "channel", -50)

        # Owner pressed "Добавить каналы" but actually added a group.
        event = _my_chat_member(-901, "supergroup", 42, "administrator")
        handled = await try_link_pending_sponsor(bot, storage, event)

        self.assertTrue(handled)
        origin = _origin_state(storage, 1, -50, 42)
        data = await origin.get_data()
        self.assertEqual(data.get("sponsors", []), [])

    async def test_not_admin_not_linked(self) -> None:
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        await self._seed_pending(storage, 1, 42, "chat", -50)

        event = _my_chat_member(-902, "supergroup", 42, "member")
        handled = await try_link_pending_sponsor(bot, storage, event)

        self.assertTrue(handled)
        origin = _origin_state(storage, 1, -50, 42)
        data = await origin.get_data()
        self.assertEqual(data.get("sponsors", []), [])
        # Still pending — bot.get_chat_member wasn't yet promoted, owner
        # can promote it and the next event will link it.
        pending = _pending_sponsor_state(storage, 1, 42)
        self.assertEqual(await pending.get_state(), PendingSponsorAddStates.awaiting_add)

    async def test_duplicate_sponsor_rejected(self) -> None:
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        origin = _origin_state(storage, 1, -50, 42)
        await origin.update_data(sponsors=[{"chat_id": -900, "type": "channel", "title": "X", "username": "x"}])
        await self._seed_pending(storage, 1, 42, "channel", -50)

        event = _my_chat_member(-900, "channel", 42, "administrator")
        handled = await try_link_pending_sponsor(bot, storage, event)

        self.assertTrue(handled)
        data = await origin.get_data()
        self.assertEqual(len(data["sponsors"]), 1)  # unchanged, no duplicate

    async def test_cap_rejected(self) -> None:
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        origin = _origin_state(storage, 1, -50, 42)
        await origin.update_data(sponsors=[
            {"chat_id": -1, "type": "channel", "title": "A", "username": "a"},
            {"chat_id": -2, "type": "channel", "title": "B", "username": "b"},
            {"chat_id": -3, "type": "channel", "title": "C", "username": "c"},
        ])
        await self._seed_pending(storage, 1, 42, "channel", -50)

        event = _my_chat_member(-900, "channel", 42, "administrator")
        handled = await try_link_pending_sponsor(bot, storage, event)

        self.assertTrue(handled)
        data = await origin.get_data()
        self.assertEqual(len(data["sponsors"]), 3)

    async def test_no_pending_marker_not_handled(self) -> None:
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        event = _my_chat_member(-900, "channel", 999, "administrator")
        handled = await try_link_pending_sponsor(bot, storage, event)
        self.assertFalse(handled)


class BonusCreationWithSponsorsTests(ChatModelsTestCase):
    async def test_sponsors_persisted_on_creation(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-7, title="T", status="active", owner_user_id=42, member_count=300))
            session.add(User(user_id=42, first_name="Owner", stars_balance=Decimal("100")))
            await session.commit()

        state = _fsm_state({
            "chat_id": -7, "code": "SP1", "reward": "1", "limit": 5, "mode": "self_serve",
            "sponsors": [
                {"chat_id": -900, "type": "channel", "title": "Ch1", "username": "ch1"},
                {"chat_id": -901, "type": "chat", "title": "Group1", "username": None},
            ],
        })
        cb = _callback(-7, 42, "chatbonus:sponsors:done")
        async with self.sessions() as session:
            await cb_bonus_sponsors_done(cb, state, session)

        async with self.sessions() as session:
            bonus = await ChatBonusRepository(session).get_by_code(-7, "SP1")
            sponsors = await ChatBonusSponsorRepository(session).list_for_bonus(bonus.id)
        self.assertEqual(len(sponsors), 2)
        self.assertEqual({s.sponsor_chat_id for s in sponsors}, {-900, -901})


class RedeemWithSponsorsTests(ChatModelsTestCase):
    async def _make_bonus_with_sponsor(self, chat_id: int, owner_id: int, sponsor_chat_id: int) -> ChatBonusCode:
        bonus = await self._make_bonus(chat_id, owner_id, code="SPBONUS")
        async with self.sessions() as session:
            session.add(ChatBonusSponsor(
                bonus_id=bonus.id, sponsor_chat_id=sponsor_chat_id, sponsor_type="channel",
                title="Sponsor", username="sponsorchan",
            ))
            await session.commit()
        return bonus

    async def test_unsubscribed_user_blocked_with_check_button(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-8, title="T", status="active", owner_user_id=1))
            session.add(User(user_id=10, first_name="U", stars_balance=Decimal("0")))
            session.add(ChatMembership(chat_id=-8, user_id=10))
            await session.commit()
        await self._make_bonus_with_sponsor(-8, 1, -8000)

        bot = _bot()
        bot.get_chat_member.return_value = SimpleNamespace(status="left")
        message = _message(-8, 10, "бонус SPBONUS")
        async with self.sessions() as session:
            await msg_bonus_redeem(message, session, bot)

        args, kwargs = message.reply.await_args
        self.assertIn("не подписаны", args[0])
        self.assertIn("chatbonus:check:", kwargs["reply_markup"].inline_keyboard[-1][0].callback_data)
        async with self.sessions() as session:
            user = await session.get(User, 10)
        self.assertEqual(user.stars_balance, Decimal("0"))  # not credited yet

    async def test_check_button_auto_activates_once_subscribed(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-9, title="T", status="active", owner_user_id=1))
            session.add(User(user_id=11, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
        bonus = await self._make_bonus_with_sponsor(-9, 1, -9000)

        bot = _bot()
        bot.get_chat_member.return_value = SimpleNamespace(status="member")  # now subscribed
        cb = _callback(-9, 11, f"chatbonus:check:{bonus.id}")
        async with self.sessions() as session:
            await cb_bonus_check_sponsors(cb, session, bot)

        cb.message.answer.assert_awaited_once()
        rendered = cb.message.answer.await_args.args[0]
        self.assertIn("получен", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 11)
        self.assertEqual(user.stars_balance, Decimal("1"))

    async def test_check_button_twice_does_not_double_credit(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-10, title="T", status="active", owner_user_id=1))
            session.add(User(user_id=12, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
        bonus = await self._make_bonus_with_sponsor(-10, 1, -10000)

        bot = _bot()
        bot.get_chat_member.return_value = SimpleNamespace(status="member")
        for _ in range(2):
            cb = _callback(-10, 12, f"chatbonus:check:{bonus.id}")
            async with self.sessions() as session:
                await cb_bonus_check_sponsors(cb, session, bot)

        async with self.sessions() as session:
            user = await session.get(User, 12)
        self.assertEqual(user.stars_balance, Decimal("1"))  # only credited once


if __name__ == "__main__":
    unittest.main()
