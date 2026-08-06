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
from bot.handlers.mychats import (
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
)
from bot.services.chat_broadcast_scheduler import _run_pass, _send_one
from bot.states.group import ChatOwnerBroadcastStates


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _callback(user_id: int, data: str):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, from_user=SimpleNamespace(id=user_id), data=data, answer=AsyncMock())


def _text_message(user_id: int, text: str):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=user_id), photo=None, answer=AsyncMock())


def _photo_message(user_id: int, file_id: str):
    photo_size = SimpleNamespace(file_id=file_id)
    return SimpleNamespace(
        text=None, photo=[photo_size], from_user=SimpleNamespace(id=user_id), answer=AsyncMock(),
    )


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class RepositoryTests(ChatModelsTestCase):
    async def test_add_list_delete_message(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            m1 = await repo.add_message(-1, "hello")
            await repo.add_message(-1, "world")
            self.assertEqual(await repo.count(-1), 2)
            messages = await repo.list_messages(-1)
            self.assertEqual([m.text for m in messages], ["hello", "world"])

            self.assertTrue(await repo.delete_message(-1, m1.id))
            self.assertEqual(await repo.count(-1), 1)
            # Wrong chat_id must not delete another chat's message.
            self.assertFalse(await repo.delete_message(-2, messages[1].id))

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

    async def test_add_text_flow_no_photos_no_buttons(self) -> None:
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

        async with self.sessions() as session:
            messages = await ChatBroadcastRepository(session).list_messages(-1)
        self.assertEqual([m.text for m in messages], ["Join our giveaway!"])
        self.assertEqual(load_photo_ids(messages[0]), [])
        self.assertEqual(load_buttons(messages[0]), [])

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

    async def test_toggle_blocked_without_texts_or_interval(self) -> None:
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

    async def test_toggle_succeeds_once_text_and_interval_present(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_interval_seconds=300,
            ))
            await session.commit()
            await ChatBroadcastRepository(session).add_message(-1, "hi")

        cb = _callback(1, "mychats:custombc:toggle:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_toggle(cb, session)

        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertTrue(chat.custom_broadcast_enabled)

    async def test_panel_shows_texts_but_no_reward_mention(self) -> None:
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
        self.assertNotIn("RP⭐️", rendered)


class SchedulerTests(ChatModelsTestCase):
    async def test_send_one_rotates_and_advances_index(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="Owner"))
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()
            repo = ChatBroadcastRepository(session)
            await repo.add_message(-1, "first")
            await repo.add_message(-1, "second")

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

    async def test_send_one_with_single_photo_uses_send_photo(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()
            await ChatBroadcastRepository(session).add_message(-1, "caption", photo_file_ids=["p1"])

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
            await ChatBroadcastRepository(session).add_message(
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
            await ChatBroadcastRepository(session).add_message(
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

    async def test_send_one_disables_broadcast_when_no_texts_left(self) -> None:
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
            repo = ChatBroadcastRepository(session)
            await repo.add_message(-1, "hi")
            await repo.add_message(-2, "hi")

        bot = SimpleNamespace(send_message=AsyncMock())
        with patch("bot.services.chat_broadcast_scheduler.SessionFactory", self.sessions):
            await _run_pass(bot)

        bot.send_message.assert_awaited_once_with(-1, "hi", reply_markup=None)


if __name__ == "__main__":
    unittest.main()
