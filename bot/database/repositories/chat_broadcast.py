from datetime import datetime

from sqlalchemy import delete, func, select

from bot.database.models import Chat, ChatBroadcastMessage
from bot.database.repositories.base import BaseRepository


class ChatBroadcastRepository(BaseRepository):
    async def list_messages(self, chat_id: int) -> list[ChatBroadcastMessage]:
        result = await self.session.execute(
            select(ChatBroadcastMessage)
            .where(ChatBroadcastMessage.chat_id == chat_id)
            .order_by(ChatBroadcastMessage.id)
        )
        return list(result.scalars().all())

    async def count(self, chat_id: int) -> int:
        result = await self.session.execute(
            select(func.count(ChatBroadcastMessage.id)).where(ChatBroadcastMessage.chat_id == chat_id)
        )
        return result.scalar_one()

    async def add_message(self, chat_id: int, text: str) -> ChatBroadcastMessage:
        message = ChatBroadcastMessage(chat_id=chat_id, text=text)
        self.session.add(message)
        await self.session.commit()
        return message

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
