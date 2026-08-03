from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from bot.database.models import Chat, ChatMembership, User
from bot.database.repositories.base import BaseRepository


class ChatRepository(BaseRepository):
    async def get(self, chat_id: int) -> Chat | None:
        return await self.session.get(Chat, chat_id)

    async def upsert(
        self,
        chat_id: int,
        title: str,
        member_count: int,
        owner_user_id: int | None,
        min_members: int,
    ) -> Chat:
        chat = await self.session.get(Chat, chat_id)
        status = "active" if member_count >= min_members else "pending"
        now = datetime.utcnow()
        if chat:
            chat.title = title
            chat.member_count = member_count
            if owner_user_id is not None:
                chat.owner_user_id = owner_user_id
            chat.status = status
            chat.left_at = None
            chat.last_admin_sync_at = now
        else:
            chat = Chat(
                chat_id=chat_id,
                title=title,
                member_count=member_count,
                owner_user_id=owner_user_id,
                status=status,
                last_admin_sync_at=now,
            )
            self.session.add(chat)
        await self.session.commit()
        return chat

    async def mark_left(self, chat_id: int) -> None:
        chat = await self.session.get(Chat, chat_id)
        if chat:
            chat.status = "left"
            chat.left_at = datetime.utcnow()
            await self.session.commit()

    async def registered_members_summary(self, chat_id: int) -> tuple[int, Decimal]:
        """(count, total stars_balance) of chat members who also have a
        private-DM User row — i.e. are "registered in Evolis"."""
        result = await self.session.execute(
            select(func.count(User.user_id), func.coalesce(func.sum(User.stars_balance), 0))
            .select_from(ChatMembership)
            .join(User, User.user_id == ChatMembership.user_id)
            .where(ChatMembership.chat_id == chat_id, ChatMembership.left_at.is_(None))
        )
        count, total = result.one()
        return int(count), Decimal(str(total))

    async def top_by_member_count(self, limit: int = 10) -> list[Chat]:
        result = await self.session.execute(
            select(Chat)
            .where(Chat.status == "active")
            .order_by(Chat.member_count.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
