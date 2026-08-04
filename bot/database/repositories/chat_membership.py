from datetime import datetime

from sqlalchemy import select, update

from bot.database.models import ChatMembership, User
from bot.database.repositories.base import BaseRepository


class ChatMembershipRepository(BaseRepository):
    async def get(self, chat_id: int, user_id: int) -> ChatMembership | None:
        result = await self.session.execute(
            select(ChatMembership).where(
                ChatMembership.chat_id == chat_id,
                ChatMembership.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def top_users_by_balance(self, chat_id: int, limit: int = 10) -> list[User]:
        result = await self.session.execute(
            select(User)
            .join(ChatMembership, ChatMembership.user_id == User.user_id)
            .where(ChatMembership.chat_id == chat_id, ChatMembership.left_at.is_(None))
            .order_by(User.stars_balance.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def touch_message(self, chat_id: int, user_id: int) -> None:
        """Upsert + atomic increment, called on every observed group message."""
        now = datetime.utcnow()
        result = await self.session.execute(
            update(ChatMembership)
            .where(ChatMembership.chat_id == chat_id, ChatMembership.user_id == user_id)
            .values(
                message_count=ChatMembership.message_count + 1,
                last_message_at=now,
                left_at=None,
            )
        )
        if result.rowcount == 0:
            self.session.add(
                ChatMembership(
                    chat_id=chat_id,
                    user_id=user_id,
                    joined_at=now,
                    message_count=1,
                    last_message_at=now,
                )
            )
        await self.session.commit()

    async def mark_joined(self, chat_id: int, user_id: int) -> None:
        existing = await self.get(chat_id, user_id)
        now = datetime.utcnow()
        if existing:
            existing.left_at = None
        else:
            self.session.add(ChatMembership(chat_id=chat_id, user_id=user_id, joined_at=now))
        await self.session.commit()

    async def mark_left(self, chat_id: int, user_id: int) -> None:
        existing = await self.get(chat_id, user_id)
        if existing:
            existing.left_at = datetime.utcnow()
            await self.session.commit()
