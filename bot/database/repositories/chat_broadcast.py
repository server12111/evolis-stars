import json
from datetime import datetime

from sqlalchemy import delete, func, select, update

from bot.database.models import Chat, ChatBroadcastMessage
from bot.database.repositories.base import BaseRepository

MAX_BROADCAST_PHOTOS = 5
MAX_BROADCAST_BUTTONS = 3


def load_photo_ids(message: ChatBroadcastMessage) -> list[str]:
    if not message.photo_file_ids:
        return []
    try:
        return json.loads(message.photo_file_ids)
    except (TypeError, ValueError):
        return []


def load_buttons(message: ChatBroadcastMessage) -> list[dict]:
    if not message.buttons_json:
        return []
    try:
        return json.loads(message.buttons_json)
    except (TypeError, ValueError):
        return []


class ChatBroadcastRepository(BaseRepository):
    async def get(self, message_id: int) -> ChatBroadcastMessage | None:
        return await self.session.get(ChatBroadcastMessage, message_id)

    async def list_messages(self, chat_id: int) -> list[ChatBroadcastMessage]:
        result = await self.session.execute(
            select(ChatBroadcastMessage)
            .where(ChatBroadcastMessage.chat_id == chat_id)
            .order_by(ChatBroadcastMessage.id)
        )
        return list(result.scalars().all())

    async def list_approved(self, chat_id: int) -> list[ChatBroadcastMessage]:
        """Only these are ever eligible to be sent by the scheduler —
        pending/rejected rows are excluded even if custom_broadcast_enabled
        is on."""
        result = await self.session.execute(
            select(ChatBroadcastMessage)
            .where(ChatBroadcastMessage.chat_id == chat_id, ChatBroadcastMessage.status == "approved")
            .order_by(ChatBroadcastMessage.id)
        )
        return list(result.scalars().all())

    async def count(self, chat_id: int) -> int:
        """Counts pending + approved rows (i.e. everything but rejected,
        which is deleted outright) — this is what the per-chat text cap is
        checked against, so a rejected submission never blocks a retry."""
        result = await self.session.execute(
            select(func.count(ChatBroadcastMessage.id)).where(ChatBroadcastMessage.chat_id == chat_id)
        )
        return result.scalar_one()

    async def count_approved(self, chat_id: int) -> int:
        result = await self.session.execute(
            select(func.count(ChatBroadcastMessage.id)).where(
                ChatBroadcastMessage.chat_id == chat_id, ChatBroadcastMessage.status == "approved",
            )
        )
        return result.scalar_one()

    async def add_message(
        self,
        chat_id: int,
        text: str,
        photo_file_ids: list[str] | None = None,
        buttons: list[dict] | None = None,
        text_is_html: bool = False,
    ) -> ChatBroadcastMessage:
        message = ChatBroadcastMessage(
            chat_id=chat_id,
            text=text,
            text_is_html=text_is_html,
            photo_file_ids=json.dumps(photo_file_ids[:MAX_BROADCAST_PHOTOS]) if photo_file_ids else None,
            buttons_json=json.dumps(buttons[:MAX_BROADCAST_BUTTONS]) if buttons else None,
            status="pending",
        )
        self.session.add(message)
        await self.session.commit()
        return message

    async def set_moderation_message_id(self, message_id: int, channel_message_id: int) -> None:
        await self.session.execute(
            update(ChatBroadcastMessage)
            .where(ChatBroadcastMessage.id == message_id)
            .values(moderation_channel_message_id=channel_message_id)
        )
        await self.session.commit()

    async def approve(self, message_id: int) -> ChatBroadcastMessage | None:
        """Returns the approved row, or None if it was already decided
        (double-tap on the moderation channel's buttons)."""
        result = await self.session.execute(
            update(ChatBroadcastMessage)
            .where(ChatBroadcastMessage.id == message_id, ChatBroadcastMessage.status == "pending")
            .values(status="approved")
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.session.get(ChatBroadcastMessage, message_id)

    async def reject(self, message_id: int) -> tuple[int, str] | None:
        """Deletes the pending row and returns its (chat_id, text) for the
        owner notification, or None if it was already decided. The delete
        is conditioned on status='pending' in the same statement (not a
        separate read-then-delete) so two admins tapping reject/approve on
        the same message at the same moment can't both "win" — without
        this, a plain read-then-delete could delete a row an approve()
        had just committed a moment earlier, double-notifying the owner
        with contradictory decisions."""
        message = await self.session.get(ChatBroadcastMessage, message_id)
        if message is None:
            return None
        chat_id, text = message.chat_id, message.text
        result = await self.session.execute(
            delete(ChatBroadcastMessage).where(
                ChatBroadcastMessage.id == message_id, ChatBroadcastMessage.status == "pending",
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.commit()
        return chat_id, text

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        result = await self.session.execute(
            delete(ChatBroadcastMessage).where(
                ChatBroadcastMessage.id == message_id,
                ChatBroadcastMessage.chat_id == chat_id,
            )
        )
        await self.session.commit()
        return result.rowcount == 1

    async def due_chats(self, now: datetime) -> list[Chat]:
        """Chats with custom broadcast enabled whose per-chat interval has
        elapsed. Filtered in Python rather than SQL — the due condition
        depends on a per-row interval, not a single fixed cutoff."""
        result = await self.session.execute(
            select(Chat).where(Chat.custom_broadcast_enabled == True, Chat.status == "active")  # noqa: E712
        )
        due = []
        for chat in result.scalars().all():
            if chat.custom_broadcast_interval_seconds is None:
                continue
            if chat.custom_broadcast_last_sent_at is None:
                due.append(chat)
                continue
            elapsed = (now - chat.custom_broadcast_last_sent_at).total_seconds()
            if elapsed >= chat.custom_broadcast_interval_seconds:
                due.append(chat)
        return due
