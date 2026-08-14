import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiogram.dispatcher.event.bases import SkipHandler

from bot.database.engine import Base
from bot.database.models import Chat, ChatSponsorWallSponsor
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_sponsor_wall import ChatSponsorWallRepository
from bot.handlers.group.onboarding import on_my_chat_member
from bot.handlers.mychats import (
    _msg_pending_sponsor_manual_confirm,
    _pending_sponsor_marker_armed,
    cb_mychats_bonus_start,
    cb_mychats_broadcast_toggle,
    cb_mychats_list,
    cb_mychats_open,
    cb_mychats_promo_start,
    cb_mychats_wallintegration,
    msg_wall_limit,
)
from bot.keyboards.main import main_menu_kb
from bot.states.group import ChatOwnerBonusStates, ChatOwnerPromoStates, ChatOwnerWallStates


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _callback(user_id: int, data: str):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=user_id, title="Private"),
        edit_text=AsyncMock(), answer=AsyncMock(),
    )
    return SimpleNamespace(message=message, from_user=SimpleNamespace(id=user_id), data=data, answer=AsyncMock())


def _message(user_id: int, text: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        text=text,
        forward_origin=None,
        forward_from_chat=None,
        answer=AsyncMock(),
    )


def _chat_member_updated(chat_id: int, user_id: int, status: str, old_status: str = "left", chat_type: str = "supergroup"):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Test Chat", username=None, type=chat_type),
        new_chat_member=SimpleNamespace(status=status, user=SimpleNamespace(id=user_id)),
        old_chat_member=SimpleNamespace(status=old_status),
        from_user=None,
    )


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class MainMenuButtonVisibilityTests(unittest.TestCase):
    def test_button_hidden_without_chats(self) -> None:
        kb = main_menu_kb(has_chats=False)
        datas = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        self.assertNotIn("mychats:list", datas)

    def test_button_shown_with_chats(self) -> None:
        kb = main_menu_kb(has_chats=True)
        datas = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        self.assertIn("mychats:list", datas)


class OwnedChatsScopingTests(ChatModelsTestCase):
    async def test_user_sees_only_own_chats(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="Mine", status="active", owner_user_id=1))
            session.add(Chat(chat_id=-2, title="Also mine", status="active", owner_user_id=1))
            session.add(Chat(chat_id=-3, title="Someone else's", status="active", owner_user_id=2))
            session.add(Chat(chat_id=-4, title="Left", status="left", owner_user_id=1))
            await session.commit()

        async with self.sessions() as session:
            chats = await ChatRepository(session).list_owned_by(1)
        self.assertEqual({c.chat_id for c in chats}, {-1, -2})

    async def test_mychats_list_shows_only_owned(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-10, title="Mine", status="active", owner_user_id=5))
            session.add(Chat(chat_id=-11, title="Other", status="active", owner_user_id=6))
            await session.commit()

        cb = _callback(5, "mychats:list")
        async with self.sessions() as session:
            await cb_mychats_list(cb, session, _state())

        rendered_kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        button_datas = {btn.callback_data for row in rendered_kb.inline_keyboard for btn in row}
        self.assertIn("mychats:open:-10", button_datas)
        self.assertNotIn("mychats:open:-11", button_datas)


class AccessControlTests(ChatModelsTestCase):
    """Item 6 — a forged callback_data chat_id must never grant access to
    someone else's chat, on any panel action."""

    async def test_open_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-20, title="T", status="active", owner_user_id=1))
            await session.commit()

        cb = _callback(999, "mychats:open:-20")
        async with self.sessions() as session:
            await cb_mychats_open(cb, session, _state())

        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        cb.message.edit_text.assert_not_awaited()

    async def test_open_allowed_for_real_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-21, title="T", status="active", owner_user_id=1, member_count=300))
            await session.commit()

        cb = _callback(1, "mychats:open:-21")
        async with self.sessions() as session:
            await cb_mychats_open(cb, session, _state())

        cb.message.edit_text.assert_awaited_once()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("-21", rendered)

    async def test_open_blocked_for_pending_chat_under_threshold(self) -> None:
        # /EvolisOpen always blocked management for a chat that hasn't
        # crossed chat_min_members yet — the panel must not silently drop
        # that gate just because it moved to a new entry point.
        async with self.sessions() as session:
            session.add(Chat(chat_id=-24, title="T", status="pending", owner_user_id=1, member_count=10))
            await session.commit()

        cb = _callback(1, "mychats:open:-24")
        async with self.sessions() as session:
            await cb_mychats_open(cb, session, _state())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("250", rendered)
        self.assertIn("10", rendered)
        # The panel keyboard (with promo/bonus/broadcast buttons) must not
        # have been rendered for a chat that hasn't crossed the threshold.
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        all_datas = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        self.assertNotIn("mychats:promo:-24", all_datas)

    async def test_none_message_does_not_crash_any_handler(self) -> None:
        # A callback whose original message is gone (deleted / inaccessible)
        # must be handled gracefully, not raise.
        for data in (
            "mychats:list", "mychats:open:-1", "mychats:refresh:-1",
            "mychats:broadcast:-1", "mychats:stats:-1",
            "mychats:games:-1", "mychats:promo:-1", "mychats:bonus:-1",
        ):
            cb = SimpleNamespace(message=None, from_user=SimpleNamespace(id=1), data=data, answer=AsyncMock())
            async with self.sessions() as session:
                # Look the handler up by name instead of importing all ten
                # at the top of the file.
                import bot.handlers.mychats as mychats_mod
                name_map = {
                    "mychats:list": "cb_mychats_list",
                    "mychats:open:-1": "cb_mychats_open",
                    "mychats:refresh:-1": "cb_mychats_refresh",
                    "mychats:broadcast:-1": "cb_mychats_broadcast_toggle",
                    "mychats:stats:-1": "cb_mychats_stats",
                    "mychats:games:-1": "cb_mychats_games",
                    "mychats:promo:-1": "cb_mychats_promo_start",
                    "mychats:bonus:-1": "cb_mychats_bonus_start",
                }
                func = getattr(mychats_mod, name_map[data])
                kwargs = {"callback": cb, "session": session}
                import inspect
                params = inspect.signature(func).parameters
                if "state" in params:
                    kwargs["state"] = _state()
                if "bot" in params:
                    kwargs["bot"] = AsyncMock()
                await func(**kwargs)
            cb.answer.assert_awaited()

    async def test_broadcast_toggle_denied_for_non_owner_even_with_correct_chat_id(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-22, title="T", status="active", owner_user_id=1, broadcast_opt_in=False))
            await session.commit()

        cb = _callback(999, "mychats:broadcast:-22")
        async with self.sessions() as session:
            await cb_mychats_broadcast_toggle(cb, session)

        async with self.sessions() as session:
            chat = await session.get(Chat, -22)
        self.assertFalse(chat.broadcast_opt_in)

    async def test_promo_start_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-23, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        cb = _callback(999, "mychats:promo:-23")
        async with self.sessions() as session:
            await cb_mychats_promo_start(cb, session, state)

        self.assertIsNone(await state.get_state())


class PromoBonusEntryPointTests(ChatModelsTestCase):
    async def test_promo_start_sets_fsm_state_with_explicit_chat_id(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-30, title="T", status="active", owner_user_id=7))
            await session.commit()

        state = _state()
        cb = _callback(7, "mychats:promo:-30")
        async with self.sessions() as session:
            await cb_mychats_promo_start(cb, session, state)

        self.assertEqual(await state.get_state(), ChatOwnerPromoStates.enter_code)
        data = await state.get_data()
        self.assertEqual(data["chat_id"], -30)

    async def test_bonus_start_sets_fsm_state_with_explicit_chat_id(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-31, title="T", status="active", owner_user_id=7))
            await session.commit()

        state = _state()
        cb = _callback(7, "mychats:bonus:-31")
        async with self.sessions() as session:
            await cb_mychats_bonus_start(cb, session, state)

        self.assertEqual(await state.get_state(), ChatOwnerBonusStates.enter_code)
        data = await state.get_data()
        self.assertEqual(data["chat_id"], -31)


class ConnectInstructionsTests(ChatModelsTestCase):
    async def test_owner_gets_dm_with_panel_buttons_on_first_connect(self) -> None:
        event = _chat_member_updated(-40, 1, "administrator")
        bot = SimpleNamespace(
            id=1,
            get_chat_member_count=AsyncMock(return_value=300),
            get_chat_administrators=AsyncMock(
                return_value=[SimpleNamespace(status="creator", user=SimpleNamespace(id=1))]
            ),
            send_message=AsyncMock(),
        )
        async with self.sessions() as session:
            await on_my_chat_member(event, bot, session, _state())

        bot.send_message.assert_awaited_once()
        args, kwargs = bot.send_message.await_args
        self.assertEqual(args[0], 1)  # DM'd to the owner
        self.assertIn("Панель чатов", args[1])
        button_datas = {b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row if b.callback_data}
        self.assertIn("mychats:list", button_datas)

    async def test_no_dm_on_promotion_not_initial_add(self) -> None:
        event = _chat_member_updated(-41, 1, "administrator", old_status="member")
        bot = SimpleNamespace(
            id=1,
            get_chat_member_count=AsyncMock(return_value=300),
            get_chat_administrators=AsyncMock(
                return_value=[SimpleNamespace(status="creator", user=SimpleNamespace(id=1))]
            ),
            send_message=AsyncMock(),
        )
        async with self.sessions() as session:
            await on_my_chat_member(event, bot, session, _state())
        bot.send_message.assert_not_awaited()


class WallIntegrationToggleTests(ChatModelsTestCase):
    async def test_owner_can_toggle_off_then_on(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-30, title="T", status="active", owner_user_id=1))
            await session.commit()

        cb = _callback(1, "mychats:wallintegration:-30")
        async with self.sessions() as session:
            await cb_mychats_wallintegration(cb, session)
        async with self.sessions() as session:
            chat = await session.get(Chat, -30)
        self.assertTrue(chat.wall_integration_enabled)  # opt-in default is off -> first press turns it on

        async with self.sessions() as session:
            await cb_mychats_wallintegration(cb, session)
        async with self.sessions() as session:
            chat = await session.get(Chat, -30)
        self.assertFalse(chat.wall_integration_enabled)

    async def test_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-31, title="T", status="active", owner_user_id=1))
            await session.commit()

        cb = _callback(999, "mychats:wallintegration:-31")
        async with self.sessions() as session:
            await cb_mychats_wallintegration(cb, session)

        async with self.sessions() as session:
            chat = await session.get(Chat, -31)
        self.assertFalse(chat.wall_integration_enabled)  # untouched, still the (opt-in) default


class WallLimitTests(ChatModelsTestCase):
    async def _seed(self, chat_id: int, owner_id: int, *, max_sponsors: int, active_sponsor_chat_ids: list[int]):
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=chat_id, title="T", status="active", owner_user_id=owner_id,
                sponsor_wall_max_sponsors=max_sponsors,
            ))
            for sponsor_chat_id in active_sponsor_chat_ids:
                session.add(ChatSponsorWallSponsor(
                    chat_id=chat_id, sponsor_chat_id=sponsor_chat_id, sponsor_type="channel",
                    title="S", username=None, is_active=True,
                ))
            await session.commit()

    async def test_lowering_below_active_count_rejected(self) -> None:
        await self._seed(-60, 1, max_sponsors=3, active_sponsor_chat_ids=[-900, -901])
        state = _state()
        await state.update_data(chat_id=-60)
        message = _message(1, "1")
        async with self.sessions() as session:
            await msg_wall_limit(message, state, session)

        message.answer.assert_awaited_once()
        self.assertIn("нельзя поставить ниже", message.answer.await_args.args[0])
        async with self.sessions() as session:
            chat = await session.get(Chat, -60)
        self.assertEqual(chat.sponsor_wall_max_sponsors, 3)  # unchanged

    async def test_lowering_to_exactly_active_count_accepted(self) -> None:
        await self._seed(-61, 1, max_sponsors=5, active_sponsor_chat_ids=[-900, -901])
        state = _state()
        await state.update_data(chat_id=-61)
        message = _message(1, "2")
        async with self.sessions() as session:
            await msg_wall_limit(message, state, session)

        async with self.sessions() as session:
            chat = await session.get(Chat, -61)
        self.assertEqual(chat.sponsor_wall_max_sponsors, 2)

    async def test_raising_the_limit_still_accepted(self) -> None:
        await self._seed(-62, 1, max_sponsors=3, active_sponsor_chat_ids=[-900])
        state = _state()
        await state.update_data(chat_id=-62)
        message = _message(1, "10")
        async with self.sessions() as session:
            await msg_wall_limit(message, state, session)

        async with self.sessions() as session:
            chat = await session.get(Chat, -62)
        self.assertEqual(chat.sponsor_wall_max_sponsors, 10)


class PendingSponsorMarkerFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_marker_rejected_without_touching_session(self) -> None:
        message = _message(999, "ping")
        bot = SimpleNamespace(id=1)
        state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=999, user_id=999))
        self.assertFalse(await _pending_sponsor_marker_armed(message, bot, state))

    async def test_armed_marker_accepted(self) -> None:
        from bot.handlers.group.chat_bonus import PendingSponsorAddStates, _pending_sponsor_state

        storage = MemoryStorage()
        pending = _pending_sponsor_state(storage, 1, 42)
        await pending.set_state(PendingSponsorAddStates.awaiting_add)

        message = _message(42, "@sponsorch")
        bot = SimpleNamespace(id=1)
        state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=42, user_id=42))
        self.assertTrue(await _pending_sponsor_marker_armed(message, bot, state))

    async def test_armed_marker_yields_to_an_unrelated_in_progress_flow(self) -> None:
        # e.g. the user is mid-withdrawal (WithdrawStates.enter_recipient_username)
        # in a router included AFTER mychats.router -- an armed sponsor marker
        # left over from up to 180s ago must not hijack that flow's own reply.
        from bot.handlers.group.chat_bonus import PendingSponsorAddStates, _pending_sponsor_state

        storage = MemoryStorage()
        pending = _pending_sponsor_state(storage, 1, 42)
        await pending.set_state(PendingSponsorAddStates.awaiting_add)

        message = _message(42, "@sponsorch")
        bot = SimpleNamespace(id=1)
        state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=42, user_id=42))
        await state.set_state("SomeUnrelatedFlow:waiting")
        self.assertFalse(await _pending_sponsor_marker_armed(message, bot, state))


class PendingSponsorManualConfirmWiringTests(ChatModelsTestCase):
    async def test_no_pending_marker_skips_to_other_handlers(self) -> None:
        message = _message(999, "hello")
        bot = SimpleNamespace(id=1, get_chat=AsyncMock(), get_chat_member=AsyncMock(), send_message=AsyncMock())
        state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=999, user_id=999))
        async with self.sessions() as session:
            with self.assertRaises(SkipHandler):
                await _msg_pending_sponsor_manual_confirm(message, session, bot, state)

    async def test_resolved_reference_does_not_skip(self) -> None:
        from bot.handlers.group.chat_bonus import PendingSponsorAddStates, _pending_sponsor_state

        storage = MemoryStorage()
        pending = _pending_sponsor_state(storage, 1, 42)
        await pending.set_state(PendingSponsorAddStates.awaiting_add)
        await pending.update_data(sponsor_type="channel", origin_chat_id=-50, purpose="bonus")

        message = _message(42, "@sponsorch")
        bot = SimpleNamespace(
            id=1,
            get_chat=AsyncMock(return_value=SimpleNamespace(id=-900, type="channel", title="S", username="sponsorch")),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
            send_message=AsyncMock(),
        )
        state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=42, user_id=42))
        async with self.sessions() as session:
            await _msg_pending_sponsor_manual_confirm(message, session, bot, state)  # must not raise


if __name__ == "__main__":
    unittest.main()
