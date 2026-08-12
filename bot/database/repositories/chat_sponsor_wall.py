from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from bot.database.models import ChatSponsorWallCompletion, ChatSponsorWallSponsor
from bot.database.repositories.base import BaseRepository


class ChatSponsorWallRepository(BaseRepository):
    async def list_active(self, chat_id: int) -> list[ChatSponsorWallSponsor]:
        result = await self.session.execute(
            select(ChatSponsorWallSponsor)
            .where(ChatSponsorWallSponsor.chat_id == chat_id, ChatSponsorWallSponsor.is_active.is_(True))
            .order_by(ChatSponsorWallSponsor.id)
        )
        return list(result.scalars().all())

    async def list_all(self, chat_id: int) -> list[ChatSponsorWallSponsor]:
        result = await self.session.execute(
            select(ChatSponsorWallSponsor)
            .where(ChatSponsorWallSponsor.chat_id == chat_id)
            .order_by(ChatSponsorWallSponsor.id)
        )
        return list(result.scalars().all())

    async def count_active(self, chat_id: int) -> int:
        result = await self.session.execute(
            select(func.count(ChatSponsorWallSponsor.id)).where(
                ChatSponsorWallSponsor.chat_id == chat_id, ChatSponsorWallSponsor.is_active.is_(True),
            )
        )
        return result.scalar() or 0

    async def get(self, sponsor_id: int) -> ChatSponsorWallSponsor | None:
        return await self.session.get(ChatSponsorWallSponsor, sponsor_id)

    async def list_previously_used_by_owner(self, owner_user_id: int, sponsor_type: str) -> list[ChatSponsorWallSponsor]:
        """Distinct past wall sponsors (across any of this owner's other
        chats) -- lets a new wall reuse one without repeating the "promote
        the bot to admin" flow, whose deep link fires no my_chat_member
        event (so silently does nothing) when the bot is already an admin
        there. See also ChatBonusSponsorRepository.list_previously_used_by_owner
        -- the add-sponsor handler merges both, since either feature could
        already have made the bot admin in the same partner channel."""
        from bot.database.models import Chat

        result = await self.session.execute(
            select(ChatSponsorWallSponsor)
            .join(Chat, Chat.chat_id == ChatSponsorWallSponsor.chat_id)
            .where(
                Chat.owner_user_id == owner_user_id,
                ChatSponsorWallSponsor.sponsor_type == sponsor_type,
            )
            .order_by(ChatSponsorWallSponsor.id.desc())
        )
        seen: set[int] = set()
        sponsors: list[ChatSponsorWallSponsor] = []
        for sponsor in result.scalars().all():
            if sponsor.sponsor_chat_id in seen:
                continue
            seen.add(sponsor.sponsor_chat_id)
            sponsors.append(sponsor)
        return sponsors

    async def add(
        self, chat_id: int, sponsor_chat_id: int, sponsor_type: str, title: str, username: str | None,
        max_sponsors: int,
    ) -> ChatSponsorWallSponsor | None:
        """Returns None (no-op) if this chat/channel is already a wall
        sponsor for this chat, or the owner's own configured cap is
        already reached."""
        existing = await self.list_active(chat_id)
        if len(existing) >= max_sponsors:
            return None
        if any(s.sponsor_chat_id == sponsor_chat_id for s in existing):
            return None
        sponsor = ChatSponsorWallSponsor(
            chat_id=chat_id, sponsor_chat_id=sponsor_chat_id, sponsor_type=sponsor_type,
            title=title, username=username,
        )
        self.session.add(sponsor)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return None
        await self.session.commit()
        return sponsor

    async def toggle(self, sponsor_id: int, chat_id: int) -> ChatSponsorWallSponsor | None:
        sponsor = await self.get(sponsor_id)
        if sponsor is None or sponsor.chat_id != chat_id:
            return None
        sponsor.is_active = not sponsor.is_active
        await self.session.commit()
        return sponsor

    async def delete(self, sponsor_id: int, chat_id: int) -> bool:
        sponsor = await self.get(sponsor_id)
        if sponsor is None or sponsor.chat_id != chat_id:
            return False
        await self.session.delete(sponsor)
        await self.session.commit()
        return True

    async def is_completed(self, sponsor_id: int, user_id: int) -> bool:
        result = await self.session.execute(
            select(ChatSponsorWallCompletion.id).where(
                ChatSponsorWallCompletion.sponsor_id == sponsor_id,
                ChatSponsorWallCompletion.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def completed_sponsor_ids(self, sponsor_ids: list[int], user_id: int) -> set[int]:
        if not sponsor_ids:
            return set()
        result = await self.session.execute(
            select(ChatSponsorWallCompletion.sponsor_id).where(
                ChatSponsorWallCompletion.sponsor_id.in_(sponsor_ids),
                ChatSponsorWallCompletion.user_id == user_id,
            )
        )
        return set(result.scalars().all())

    async def mark_completed(self, sponsor_id: int, user_id: int) -> bool:
        """Race-safe: the unique constraint on (sponsor_id, user_id) is the
        actual guard against a double credit, not the is_completed() probe
        above (which is just a cheap fast-path to skip the insert/rollback
        round-trip in the overwhelmingly common repeat-check case)."""
        if await self.is_completed(sponsor_id, user_id):
            return False
        self.session.add(ChatSponsorWallCompletion(sponsor_id=sponsor_id, user_id=user_id))
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return False
        await self.session.commit()
        return True
