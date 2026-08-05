from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

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
            try:
                await self.session.flush()
            except IntegrityError:
                # A concurrent first message from the same brand-new member
                # (no per-user lock protects group chats) already inserted
                # this row — fall back to the atomic increment now that it
                # exists, instead of letting the conflict propagate out of
                # GroupActivityMiddleware and abort this update entirely.
                await self.session.rollback()
                await self.session.execute(
                    update(ChatMembership)
                    .where(ChatMembership.chat_id == chat_id, ChatMembership.user_id == user_id)
                    .values(
                        message_count=ChatMembership.message_count + 1,
                        last_message_at=now,
                        left_at=None,
                    )
                )
        await self.session.commit()

    async def mark_joined(self, chat_id: int, user_id: int) -> None:
        """Telegram can fire more than one ChatMemberUpdated event for the
        same user in quick succession (e.g. joined then immediately
        promoted) with no per-user lock protecting group-chat updates, so
        two calls for a brand-new member can both see no existing row and
        both try to insert — handled the same way as touch_message's
        insert-or-ignore."""
        existing = await self.get(chat_id, user_id)
        now = datetime.utcnow()
        if existing:
            existing.left_at = None
            await self.session.commit()
            return
        self.session.add(ChatMembership(chat_id=chat_id, user_id=user_id, joined_at=now))
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get(chat_id, user_id)
            if existing:
                existing.left_at = None
                await self.session.commit()

    async def mark_left(self, chat_id: int, user_id: int) -> None:
        existing = await self.get(chat_id, user_id)
        if existing:
            existing.left_at = datetime.utcnow()
            await self.session.commit()
