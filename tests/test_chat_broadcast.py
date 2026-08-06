import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, User
from bot.database.repositories.chat_broadcast import (
    ChatBroadcastRepository,
    load_buttons,
    load_photo_ids,
)
from bot.handlers.admin_channel import cb_broadcast_mod_approve, cb_broadcast_mod_reject
from bot.handlers.mychats import (
    _post_broadcast_for_moderation,
    cb_custom_broadcast_add_start,
    cb_custom_broadcast_buttons_no,
    cb_custom_broadcast_buttons_done,
    cb_custom_broadcast_buttons_yes,
    cb_custom_broadcast_delete,
    cb_custom_broadcast_interval_start,
    cb_custom_broadcast_panel,
    cb_custom_broadcast_photos_next,
    cb_custom_broadcast_toggle,
    msg_custom_broadcast_button,
    msg_custom_broadcast_interval,
    msg_custom_broadcast_photo,
    msg_custom_broadcast_text,
    settings as mychats_settings,
)
from bot.services.chat_broadcast_scheduler import _run_pass, _send_one
from bot.states.group import ChatOwnerBroadcastStates


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _bot():
    return SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=999)),
        send_photo=AsyncMock(),
        send_media_group=AsyncMock(),
    )


def _callback(user_id: int, data: str, bot=None):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), text="post")
    return SimpleNamespace(
        message=message, from_user=SimpleNamespace(id=user_id), data=data,
        answer=AsyncMock(), bot=bot or _bot(),
    )


def _text_message(user_id: int, text: str, bot=None):
    return SimpleNamespace(
        text=text, from_user=SimpleNamespace(id=user_id), photo=None, answer=AsyncMock(), bot=bot or _bot(),
    )


def _photo_message(user_id: int, file_id: str, bot=None):
    photo_size = SimpleNamespace(file_id=file_id)
    return SimpleNamespace(
        text=None, photo=[photo_size], from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(), bot=bot or _bot(),
    )


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _add_approved_message(self, chat_id: int, text: str, **kwargs):
        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            message = await repo.add_message(chat_id, text, **kwargs)
            approved = await repo.approve(message.id)
        return approved


class RepositoryTests(ChatModelsTestCase):
    async def test_add_defaults_to_pending_and_list_approved_excludes_it(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            msg = await repo.add_message(-1, "hello")
            self.assertEqual(msg.status, "pending")
            self.assertEqual(await repo.count(-1), 1)
            self.assertEqual(await repo.count_approved(-1), 0)
            self.assertEqual(await repo.list_approved(-1), [])

    async def test_approve_then_reject_flow(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            msg = await repo.add_message(-1, "hello")
            approved = await repo.approve(msg.id)
            self.assertEqual(approved.status, "approved")
            self.assertEqual(await repo.count_approved(-1), 1)

            # A second approve on the same (already-decided) row is a no-op.
            self.assertIsNone(await repo.approve(msg.id))

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            msg2 = await repo.add_message(-1, "world")
            result = await repo.reject(msg2.id)
            self.assertEqual(result, (-1, "world"))
            self.assertIsNone(await repo.get(msg2.id))  # rejected rows are deleted
            self.assertIsNone(await repo.reject(msg2.id))  # already gone

    async def test_reject_cannot_delete_an_already_approved_message(self) -> None:
        """Regression: reject() used to read status, then unconditionally
        delete — if approve() committed in between (two admins racing on
        the same message), the plain delete would still remove the row
        even though it was no longer pending, silently un-approving a
        message that had just gone live and double-notifying the owner
        with contradictory decisions. The delete must be conditioned on
        status='pending' in the same statement."""
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            msg = await ChatBroadcastRepository(session).add_message(-1, "hello")

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            await repo.approve(msg.id)  # another admin approved it first

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            result = await repo.reject(msg.id)

        self.assertIsNone(result)
        async with self.sessions() as session:
            stored = await ChatBroadcastRepository(session).get(msg.id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, "approved")

    async def test_delete_message(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            m1 = await repo.add_message(-1, "hello")
            self.assertTrue(await repo.delete_message(-1, m1.id))
            self.assertEqual(await repo.count(-1), 0)
            self.assertFalse(await repo.delete_message(-1, m1.id))

    async def test_add_message_with_photos_and_buttons(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            msg = await repo.add_message(
                -1, "hello",
                photo_file_ids=["p1", "p2"],
                buttons=[{"text": "Go", "url": "https://example.com"}],
            )
        self.assertEqual(load_photo_ids(msg), ["p1", "p2"])
        self.assertEqual(load_buttons(msg), [{"text": "Go", "url": "https://example.com"}])

    async def test_due_chats_respects_interval_and_last_sent(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="Never sent", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            session.add(Chat(
                chat_id=-2, title="Due", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
                custom_broadcast_last_sent_at=now - timedelta(seconds=120),
            ))
            session.add(Chat(
                chat_id=-3, title="Not due", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=600,
                custom_broadcast_last_sent_at=now - timedelta(seconds=30),
            ))
            session.add(Chat(
                chat_id=-4, title="Disabled", status="active", owner_user_id=1,
                custom_broadcast_enabled=False, custom_broadcast_interval_seconds=1,
            ))
            session.add(Chat(
                chat_id=-5, title="No interval yet", status="active", owner_user_id=1,
                custom_broadcast_enabled=True,
            ))
            await session.commit()

        async with self.sessions() as session:
            due = await ChatBroadcastRepository(session).due_chats(now)
        self.assertEqual({c.chat_id for c in due}, {-1, -2})


class KeywordFilterTests(ChatModelsTestCase):
    async def test_banned_text_is_rejected_before_saving(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_text)
        await state.update_data(chat_id=-1)

        msg = _text_message(1, "заходи, у нас порно видео")
        async with self.sessions() as session:
            await msg_custom_broadcast_text(msg, session, state)

        self.assertIn("запрещённый контент", msg.answer.await_args.args[0])
        self.assertEqual(await state.get_state(), ChatOwnerBroadcastStates.enter_text)  # can retry
        async with self.sessions() as session:
            self.assertEqual(await ChatBroadcastRepository(session).count(-1), 0)

    async def test_evasion_spacing_is_still_caught(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_text)
        await state.update_data(chat_id=-1)

        msg = _text_message(1, "с.е.к.с знакомства тут")
        async with self.sessions() as session:
            await msg_custom_broadcast_text(msg, session, state)

        self.assertIn("запрещённый контент", msg.answer.await_args.args[0])

    async def test_ordinary_promotional_text_passes(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_text)
        await state.update_data(chat_id=-1)

        msg = _text_message(1, "Подпишись на канал и получи бонус!")
        async with self.sessions() as session:
            await msg_custom_broadcast_text(msg, session, state)

        self.assertEqual(await state.get_state(), ChatOwnerBroadcastStates.enter_photos)

    async def test_banned_button_label_is_rejected(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_button)
        await state.update_data(chat_id=-1, pending_text="X", pending_photos=[], pending_buttons=[])

        msg = _text_message(1, "порно - https://example.com")
        async with self.sessions() as session:
            await msg_custom_broadcast_button(msg, session, state)

        self.assertIn("запрещённый контент", msg.answer.await_args.args[0])
        data = await state.get_data()
        self.assertEqual(data["pending_buttons"], [])


class HandlerAccessControlTests(ChatModelsTestCase):
    async def test_add_start_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        cb = _callback(999, "mychats:custombc:add:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_add_start(cb, session, state)

        self.assertIsNone(await state.get_state())
        cb.message.edit_text.assert_not_awaited()

    async def test_add_text_flow_no_photos_no_buttons_goes_pending(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        cb = _callback(1, "mychats:custombc:add:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_add_start(cb, session, state)
        self.assertEqual(await state.get_state(), ChatOwnerBroadcastStates.enter_text)

        msg = _text_message(1, "Join our giveaway!")
        async with self.sessions() as session:
            await msg_custom_broadcast_text(msg, session, state)
        self.assertEqual(await state.get_state(), ChatOwnerBroadcastStates.enter_photos)

        photos_next_cb = _callback(1, "mychats:custombc:photos:next:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_photos_next(photos_next_cb, session, state)
        self.assertEqual(await state.get_state(), ChatOwnerBroadcastStates.ask_buttons)

        no_buttons_cb = _callback(1, "mychats:custombc:buttons:no:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_buttons_no(no_buttons_cb, session, state)
        self.assertIsNone(await state.get_state())
        self.assertIn("модерацию", no_buttons_cb.answer.await_args.args[0])

        async with self.sessions() as session:
            messages = await ChatBroadcastRepository(session).list_messages(-1)
        self.assertEqual([m.text for m in messages], ["Join our giveaway!"])
        self.assertEqual(messages[0].status, "pending")

    async def test_add_text_flow_with_photos_and_buttons(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_text)
        await state.update_data(chat_id=-1)

        msg = _text_message(1, "Check this out!")
        async with self.sessions() as session:
            await msg_custom_broadcast_text(msg, session, state)
        self.assertEqual(await state.get_state(), ChatOwnerBroadcastStates.enter_photos)

        for file_id in ("p1", "p2"):
            photo_msg = _photo_message(1, file_id)
            async with self.sessions() as session:
                await msg_custom_broadcast_photo(photo_msg, session, state)
        data = await state.get_data()
        self.assertEqual(data["pending_photos"], ["p1", "p2"])

        photos_next_cb = _callback(1, "mychats:custombc:photos:next:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_photos_next(photos_next_cb, session, state)

        yes_cb = _callback(1, "mychats:custombc:buttons:yes:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_buttons_yes(yes_cb, session, state)
        self.assertEqual(await state.get_state(), ChatOwnerBroadcastStates.enter_button)

        button_msg = _text_message(1, "Go - https://example.com")
        async with self.sessions() as session:
            await msg_custom_broadcast_button(button_msg, session, state)

        done_cb = _callback(1, "mychats:custombc:buttons:done:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_buttons_done(done_cb, session, state)
        self.assertIsNone(await state.get_state())

        async with self.sessions() as session:
            messages = await ChatBroadcastRepository(session).list_messages(-1)
        self.assertEqual(load_photo_ids(messages[0]), ["p1", "p2"])
        self.assertEqual(load_buttons(messages[0]), [{"text": "Go", "url": "https://example.com"}])
        self.assertEqual(messages[0].status, "pending")

    async def test_posts_to_moderation_channel_when_configured(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="Cool Chat", status="active", owner_user_id=1))
            session.add(User(user_id=1, first_name="Owner", username="owner1"))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_text)
        await state.update_data(chat_id=-1)

        bot = _bot()
        msg = _text_message(1, "Join our giveaway!", bot=bot)
        with patch.object(mychats_settings, "broadcast_moderation_channel_id", "-100999"):
            async with self.sessions() as session:
                await msg_custom_broadcast_text(msg, session, state)
            photos_next_cb = _callback(1, "mychats:custombc:photos:next:-1", bot=bot)
            async with self.sessions() as session:
                await cb_custom_broadcast_photos_next(photos_next_cb, session, state)
            no_buttons_cb = _callback(1, "mychats:custombc:buttons:no:-1", bot=bot)
            async with self.sessions() as session:
                await cb_custom_broadcast_buttons_no(no_buttons_cb, session, state)

        bot.send_message.assert_awaited_once()
        args, kwargs = bot.send_message.await_args
        self.assertEqual(args[0], -100999)
        self.assertIn("Join our giveaway!", args[1])
        self.assertIn("Cool Chat", args[1])
        self.assertIn("@owner1", args[1])
        kb = kwargs["reply_markup"]
        callback_datas = {b.callback_data for row in kb.inline_keyboard for b in row}
        self.assertTrue(any(d.startswith("broadcast_mod:approve:") for d in callback_datas))
        self.assertTrue(any(d.startswith("broadcast_mod:reject:") for d in callback_datas))

        async with self.sessions() as session:
            messages = await ChatBroadcastRepository(session).list_messages(-1)
        self.assertEqual(messages[0].moderation_channel_message_id, 999)

    async def test_moderation_post_attaches_a_single_photo(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        bot = _bot()
        with patch.object(mychats_settings, "broadcast_moderation_channel_id", "-100999"):
            async with self.sessions() as session:
                repo = ChatBroadcastRepository(session)
                msg = await repo.add_message(-1, "hello", photo_file_ids=["p1"])
                chat = await session.get(Chat, -1)
                await _post_broadcast_for_moderation(bot, session, chat, msg)

        bot.send_photo.assert_awaited_once_with(-100999, "p1")
        bot.send_media_group.assert_not_awaited()
        bot.send_message.assert_awaited_once()  # the separate text+buttons message
        rendered = bot.send_message.await_args.args[1]
        self.assertIn("Фото приложены выше", rendered)

    async def test_moderation_post_attaches_multiple_photos_as_an_album(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        bot = _bot()
        with patch.object(mychats_settings, "broadcast_moderation_channel_id", "-100999"):
            async with self.sessions() as session:
                repo = ChatBroadcastRepository(session)
                msg = await repo.add_message(-1, "hello", photo_file_ids=["p1", "p2"])
                chat = await session.get(Chat, -1)
                await _post_broadcast_for_moderation(bot, session, chat, msg)

        bot.send_media_group.assert_awaited_once()
        media = bot.send_media_group.await_args.args[1]
        self.assertEqual([m.media for m in media], ["p1", "p2"])
        bot.send_photo.assert_not_awaited()
        bot.send_message.assert_awaited_once()

    async def test_more_than_5_photos_ignored(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_photos)
        await state.update_data(chat_id=-1, pending_text="X", pending_photos=[])

        for i in range(6):
            photo_msg = _photo_message(1, f"p{i}")
            async with self.sessions() as session:
                await msg_custom_broadcast_photo(photo_msg, session, state)

        data = await state.get_data()
        self.assertEqual(len(data["pending_photos"]), 5)

    async def test_button_bad_format_rejected(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_button)
        await state.update_data(chat_id=-1, pending_text="X", pending_buttons=[])

        msg = _text_message(1, "not a valid button")
        async with self.sessions() as session:
            await msg_custom_broadcast_button(msg, session, state)

        msg.answer.assert_awaited_once()
        self.assertIn("Неверный формат", msg.answer.await_args.args[0])
        data = await state.get_data()
        self.assertEqual(data["pending_buttons"], [])

    async def test_third_button_auto_finalizes(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.set_state(ChatOwnerBroadcastStates.enter_button)
        await state.update_data(chat_id=-1, pending_text="X", pending_photos=[], pending_buttons=[])

        for i in range(3):
            msg = _text_message(1, f"B{i} - https://example.com/{i}")
            async with self.sessions() as session:
                await msg_custom_broadcast_button(msg, session, state)

        self.assertIsNone(await state.get_state())  # auto-finalized at the cap
        async with self.sessions() as session:
            messages = await ChatBroadcastRepository(session).list_messages(-1)
        self.assertEqual(len(load_buttons(messages[0])), 3)

    async def test_delete_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            msg_row = await ChatBroadcastRepository(session).add_message(-1, "hi")

        cb = _callback(999, f"mychats:custombc:del:-1:{msg_row.id}")
        async with self.sessions() as session:
            await cb_custom_broadcast_delete(cb, session)

        async with self.sessions() as session:
            self.assertEqual(await ChatBroadcastRepository(session).count(-1), 1)

    async def test_one_chat_one_text_limit(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            await ChatBroadcastRepository(session).add_message(-1, "existing")

        state = _state()
        cb = _callback(1, "mychats:custombc:add:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_add_start(cb, session, state)

        self.assertIsNone(await state.get_state())
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))

    async def test_interval_start_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        cb = _callback(999, "mychats:custombc:interval:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_interval_start(cb, session, state)

        self.assertIsNone(await state.get_state())
        cb.message.edit_text.assert_not_awaited()

    async def test_interval_below_minimum_rejected(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.update_data(chat_id=-1)
        await state.set_state(ChatOwnerBroadcastStates.enter_interval)
        msg = _text_message(1, "5")
        async with self.sessions() as session:
            await msg_custom_broadcast_interval(msg, session, state)

        msg.answer.assert_awaited_once()
        self.assertIn("не может быть меньше", msg.answer.await_args.args[0])
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertIsNone(chat.custom_broadcast_interval_seconds)

    async def test_interval_valid_is_saved(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.update_data(chat_id=-1)
        await state.set_state(ChatOwnerBroadcastStates.enter_interval)
        msg = _text_message(1, "600")
        async with self.sessions() as session:
            await msg_custom_broadcast_interval(msg, session, state)

        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertEqual(chat.custom_broadcast_interval_seconds, 600)
        self.assertIsNone(await state.get_state())

    async def test_toggle_blocked_without_approved_text_or_interval(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        cb = _callback(1, "mychats:custombc:toggle:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_toggle(cb, session)

        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertFalse(chat.custom_broadcast_enabled)

    async def test_toggle_blocked_while_only_pending_not_approved(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_interval_seconds=300,
            ))
            await session.commit()
            await ChatBroadcastRepository(session).add_message(-1, "hi")  # still pending

        cb = _callback(1, "mychats:custombc:toggle:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_toggle(cb, session)

        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        self.assertIn("одобренный", cb.answer.await_args.args[0])
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertFalse(chat.custom_broadcast_enabled)

    async def test_toggle_succeeds_once_approved_text_and_interval_present(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_interval_seconds=300,
            ))
            await session.commit()
        await self._add_approved_message(-1, "hi")

        cb = _callback(1, "mychats:custombc:toggle:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_toggle(cb, session)

        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertTrue(chat.custom_broadcast_enabled)

    async def test_panel_shows_status_but_no_reward_mention(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            await ChatBroadcastRepository(session).add_message(-1, "Come play!")

        cb = _callback(1, "mychats:custombc:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_panel(cb, session, _state())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Come play!", rendered)
        self.assertIn("бесплатная функция", rendered)
        self.assertIn("на модерации", rendered)
        self.assertNotIn("RP⭐️", rendered)


class ModerationDecisionTests(ChatModelsTestCase):
    async def test_approve_activates_message_and_notifies_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            msg = await ChatBroadcastRepository(session).add_message(-1, "hello")

        admin_bot = AsyncMock()
        cb = SimpleNamespace(
            data=f"broadcast_mod:approve:{msg.id}",
            from_user=SimpleNamespace(id=1),
            message=SimpleNamespace(edit_text=AsyncMock(), text="original post"),
            bot=admin_bot,
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_channel.settings.admin_ids", "1"):
            async with self.sessions() as session:
                await cb_broadcast_mod_approve(cb, session)

        async with self.sessions() as session:
            stored = await ChatBroadcastRepository(session).get(msg.id)
        self.assertEqual(stored.status, "approved")
        admin_bot.send_message.assert_awaited_once()
        self.assertEqual(admin_bot.send_message.await_args.args[0], 1)  # DM to owner
        self.assertIn("прошёл проверку", admin_bot.send_message.await_args.args[1])
        self.assertIn("Включите рассылку", admin_bot.send_message.await_args.args[1])
        cb.message.edit_text.assert_awaited_once()
        self.assertIn("Одобрено", cb.message.edit_text.await_args.args[0])

    async def test_approve_tells_owner_broadcast_already_resumed_if_enabled(self) -> None:
        """If the chat was already actively broadcasting (this text
        replaced a previously-approved, already-toggled-on one), approval
        genuinely resumes sending right away — the DM should say so
        instead of asking the owner to flip a toggle that's already on."""
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=300,
            ))
            await session.commit()
            msg = await ChatBroadcastRepository(session).add_message(-1, "hello")

        admin_bot = AsyncMock()
        cb = SimpleNamespace(
            data=f"broadcast_mod:approve:{msg.id}",
            from_user=SimpleNamespace(id=1),
            message=SimpleNamespace(edit_text=AsyncMock(), text="original post"),
            bot=admin_bot,
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_channel.settings.admin_ids", "1"):
            async with self.sessions() as session:
                await cb_broadcast_mod_approve(cb, session)

        self.assertIn("рассылка запущена", admin_bot.send_message.await_args.args[1])

    async def test_reject_deletes_message_and_notifies_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            msg = await ChatBroadcastRepository(session).add_message(-1, "hello")

        admin_bot = AsyncMock()
        cb = SimpleNamespace(
            data=f"broadcast_mod:reject:{msg.id}",
            from_user=SimpleNamespace(id=1),
            message=SimpleNamespace(edit_text=AsyncMock(), text="original post"),
            bot=admin_bot,
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_channel.settings.admin_ids", "1"):
            async with self.sessions() as session:
                await cb_broadcast_mod_reject(cb, session)

        async with self.sessions() as session:
            self.assertIsNone(await ChatBroadcastRepository(session).get(msg.id))
        admin_bot.send_message.assert_awaited_once()
        self.assertEqual(admin_bot.send_message.await_args.args[0], 1)
        self.assertIn("отказано", admin_bot.send_message.await_args.args[1])
        self.assertIn("Отклонено", cb.message.edit_text.await_args.args[0])

    async def test_non_admin_cannot_approve(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            msg = await ChatBroadcastRepository(session).add_message(-1, "hello")

        cb = SimpleNamespace(
            data=f"broadcast_mod:approve:{msg.id}",
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(edit_text=AsyncMock(), text="original post"),
            bot=AsyncMock(),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_channel.settings.admin_ids", "1"):
            async with self.sessions() as session:
                await cb_broadcast_mod_approve(cb, session)

        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        async with self.sessions() as session:
            stored = await ChatBroadcastRepository(session).get(msg.id)
        self.assertEqual(stored.status, "pending")

    async def test_double_approve_is_a_no_op(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            msg = await ChatBroadcastRepository(session).add_message(-1, "hello")

        def _cb():
            return SimpleNamespace(
                data=f"broadcast_mod:approve:{msg.id}",
                from_user=SimpleNamespace(id=1),
                message=SimpleNamespace(edit_text=AsyncMock(), text="original post"),
                bot=AsyncMock(),
                answer=AsyncMock(),
            )

        with patch("bot.handlers.admin_channel.settings.admin_ids", "1"):
            async with self.sessions() as session:
                await cb_broadcast_mod_approve(_cb(), session)
            second = _cb()
            async with self.sessions() as session:
                await cb_broadcast_mod_approve(second, session)

        self.assertTrue(second.answer.await_args.kwargs.get("show_alert"))
        second.bot.send_message.assert_not_awaited()


class SchedulerTests(ChatModelsTestCase):
    async def test_send_one_rotates_and_advances_index(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="Owner"))
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()
        await self._add_approved_message(-1, "first")
        await self._add_approved_message(-1, "second")

        bot = SimpleNamespace(send_message=AsyncMock())
        now = datetime.utcnow()
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot, session, chat, now)

        bot.send_message.assert_awaited_once_with(-1, "first", reply_markup=None)
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertEqual(chat.custom_broadcast_next_index, 1)
        self.assertEqual(chat.custom_broadcast_last_sent_at, now)

        # Second call rotates to the other text.
        bot2 = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot2, session, chat, now)
        bot2.send_message.assert_awaited_once_with(-1, "second", reply_markup=None)

    async def test_pending_only_message_is_not_sent_and_does_not_disable(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()
            await ChatBroadcastRepository(session).add_message(-1, "awaiting review")

        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot, session, chat, datetime.utcnow())

        bot.send_message.assert_not_awaited()
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertTrue(chat.custom_broadcast_enabled)  # NOT disabled — just waiting

    async def test_send_one_with_single_photo_uses_send_photo(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()
        await self._add_approved_message(-1, "caption", photo_file_ids=["p1"])

        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot, session, chat, datetime.utcnow())

        bot.send_photo.assert_awaited_once_with(-1, "p1", caption="caption", reply_markup=None)
        bot.send_message.assert_not_awaited()

    async def test_send_one_with_multiple_photos_uses_media_group_and_buttons_follow_up(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()
        await self._add_approved_message(
            -1, "caption", photo_file_ids=["p1", "p2"],
            buttons=[{"text": "Go", "url": "https://example.com"}],
        )

        bot = SimpleNamespace(send_message=AsyncMock(), send_media_group=AsyncMock())
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot, session, chat, datetime.utcnow())

        bot.send_media_group.assert_awaited_once()
        media = bot.send_media_group.await_args.args[1]
        self.assertEqual(len(media), 2)
        self.assertEqual(media[0].caption, "caption")
        self.assertIsNone(media[1].caption)
        # Buttons can't attach to a media group — sent as a follow-up.
        bot.send_message.assert_awaited_once()
        kb = bot.send_message.await_args.kwargs["reply_markup"]
        self.assertEqual(kb.inline_keyboard[0][0].url, "https://example.com")

    async def test_album_success_with_failed_buttons_followup_still_advances(self) -> None:
        """Regression: if the album itself sends fine but the buttons
        follow-up message fails, the round must still be marked sent —
        otherwise the whole album gets resent next pass over a missing
        button."""
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()
        await self._add_approved_message(
            -1, "caption", photo_file_ids=["p1", "p2"],
            buttons=[{"text": "Go", "url": "https://example.com"}],
        )

        bot = SimpleNamespace(
            send_media_group=AsyncMock(),
            send_message=AsyncMock(side_effect=Exception("rate limited")),
        )
        now = datetime.utcnow()
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot, session, chat, now)

        bot.send_media_group.assert_awaited_once()  # album still went out
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        # Marked as sent, not stuck retrying forever over the missing button.
        self.assertEqual(chat.custom_broadcast_last_sent_at, now)

    async def test_send_one_disables_broadcast_when_no_texts_left_at_all(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot, session, chat, datetime.utcnow())

        bot.send_message.assert_not_awaited()
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertFalse(chat.custom_broadcast_enabled)

    async def test_run_pass_only_sends_to_due_chats(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="Due", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
                custom_broadcast_last_sent_at=now - timedelta(seconds=120),
            ))
            session.add(Chat(
                chat_id=-2, title="Not due", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=600,
                custom_broadcast_last_sent_at=now,
            ))
            await session.commit()
        await self._add_approved_message(-1, "hi")
        await self._add_approved_message(-2, "hi")

        bot = SimpleNamespace(send_message=AsyncMock())
        with patch("bot.services.chat_broadcast_scheduler.SessionFactory", self.sessions):
            await _run_pass(bot)

        bot.send_message.assert_awaited_once_with(-1, "hi", reply_markup=None)


if __name__ == "__main__":
    unittest.main()
