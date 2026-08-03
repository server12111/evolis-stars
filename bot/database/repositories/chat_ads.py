from datetime import datetime

from sqlalchemy import func, select

from bot.database.models import ChatAdSend
from bot.database.repositories.base import BaseRepository


class ChatAdRepository(BaseRepository):
    async def record_send(self, chat_id: int, user_id: int) -> None:
        self.session.add(ChatAdSend(chat_id=chat_id, user_id=user_id))
        await self.session.commit()

    async def count_for_chat(self, chat_id: int) -> int:
        result = await self.session.execute(
            select(func.count(ChatAdSend.id)).where(ChatAdSend.chat_id == chat_id)
        )
        return int(result.scalar_one())

    async def last_sent_at(self, user_id: int) -> datetime | None:
        result = await self.session.execute(
            select(func.max(ChatAdSend.sent_at)).where(ChatAdSend.user_id == user_id)
        )
        return result.scalar_one_or_none()
