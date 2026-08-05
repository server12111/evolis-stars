from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from bot.database.models import Chat, ChatMembership, User
from bot.database.repositories.base import BaseRepository


class ChatRepository(BaseRepository):
    async def get(self, chat_id: int) -> Chat | None:
        return await self.session.get(Chat, chat_id)

    async def ensure_exists(self, chat_id: int, title: str = "") -> Chat:
        """Cheap idempotent stub-creation for GroupActivityMiddleware, which
        runs on every group message. A chat the bot was already a member of
        before this feature shipped never fires my_chat_member's "added"
        event (that only fires on the actual join), so nothing else
        guarantees a Chat row exists before ChatMembership's FK needs one —
        confirmed live: every message in a pre-existing chat raised a
        FOREIGN KEY IntegrityError without this. Leaves status/member_count/
        owner untouched if the row already exists; on_my_chat_member and
        /EvolisOpen are still what fill those in properly via upsert()."""
        chat = await self.session.get(Chat, chat_id)
        if chat is not None:
            return chat
        chat = Chat(chat_id=chat_id, title=title, status="pending")
        self.session.add(chat)
        try:
            await self.session.flush()
            await self.session.commit()
        except IntegrityError:
            # Lost the create race against a concurrent message in the same
            # brand-new chat — the other insert already won, just use it.
            await self.session.rollback()
            chat = await self.session.get(Chat, chat_id)
        return chat

    async def upsert(
        self,
        chat_id: int,
        title: str,
        member_count: int,
        owner_user_id: int | None,
        min_members: int,
        username: str | None = None,
    ) -> Chat:
        chat = await self.session.get(Chat, chat_id)
        status = "active" if member_count >= min_members else "pending"
        now = datetime.utcnow()
        if chat:
            chat.title = title
            if username is not None:
                chat.username = username
            chat.member_count = member_count
            if owner_user_id is not None:
                chat.owner_user_id = owner_user_id
            chat.status = status
            chat.left_at = None
            chat.last_admin_sync_at = now
            await self.session.commit()
            return chat

        chat = Chat(
            chat_id=chat_id,
            title=title,
            username=username,
            member_count=member_count,
            owner_user_id=owner_user_id,
            status=status,
            last_admin_sync_at=now,
        )
        self.session.add(chat)
        try:
            await self.session.commit()
        except IntegrityError:
            # Telegram can fire more than one my_chat_member event for the
            # same brand-new chat in quick succession (e.g. added then
            # immediately promoted to admin) — no lock protects this event
            # type, so two calls can both see no existing row and both try
            # to insert. Fall back to updating the row the other call won.
            await self.session.rollback()
            chat = await self.session.get(Chat, chat_id)
            chat.title = title
            if username is not None:
                chat.username = username
            chat.member_count = member_count
            if owner_user_id is not None:
                chat.owner_user_id = owner_user_id
            chat.status = status
            chat.left_at = None
            chat.last_admin_sync_at = now
            await self.session.commit()
        return chat

    async def set_invite_link_if_missing(self, chat_id: int, invite_link: str) -> None:
        """Only ever writes an invite link we read from Telegram (getChat) —
        never generates or revokes one — and only if the chat doesn't
        already have one saved, so it's never overwritten on every
        reconnect/refresh."""
        chat = await self.session.get(Chat, chat_id)
        if chat is not None and not chat.invite_link:
            chat.invite_link = invite_link
            await self.session.commit()

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

    async def connected_count(self) -> int:
        result = await self.session.execute(
            select(func.count(Chat.chat_id)).where(Chat.status != "left")
        )
        return int(result.scalar() or 0)

    async def paginated_by_member_count(self, offset: int, limit: int) -> list[Chat]:
        """All connected (never-left) chats, most members first — the admin
        chat-list panel's ranking + pagination source."""
        result = await self.session.execute(
            select(Chat)
            .where(Chat.status != "left")
            .order_by(Chat.member_count.desc(), Chat.chat_id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_owned_by(self, owner_user_id: int) -> list[Chat]:
        result = await self.session.execute(
            select(Chat)
            .where(Chat.owner_user_id == owner_user_id, Chat.status != "left")
            .order_by(Chat.title)
        )
        return list(result.scalars().all())

    async def has_owned_chats(self, owner_user_id: int) -> bool:
        result = await self.session.execute(
            select(Chat.chat_id)
            .where(Chat.owner_user_id == owner_user_id, Chat.status != "left")
            .limit(1)
        )
        return result.first() is not None

    async def all_active_chat_ids(self, opted_in_only: bool = False) -> list[int]:
        query = select(Chat.chat_id).where(Chat.status == "active")
        if opted_in_only:
            query = query.where(Chat.broadcast_opt_in == True)  # noqa: E712
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]
