from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from bot.database.models import ChatLinkButton, ChatLinkClick
from bot.database.repositories.base import BaseRepository


class LinkButtonRepository(BaseRepository):
    async def get(self, link_id: int) -> ChatLinkButton | None:
        return await self.session.get(ChatLinkButton, link_id)

    async def create(self, label: str, destination_url: str, created_by: int) -> ChatLinkButton:
        button = ChatLinkButton(label=label, destination_url=destination_url, created_by=created_by)
        self.session.add(button)
        await self.session.commit()
        return button

    async def all_active(self) -> list[ChatLinkButton]:
        result = await self.session.execute(
            select(ChatLinkButton)
            .where(ChatLinkButton.is_active == True)  # noqa: E712
            .order_by(ChatLinkButton.id.desc())
        )
        return list(result.scalars().all())

    async def delete(self, link_id: int) -> bool:
        button = await self.session.get(ChatLinkButton, link_id)
        if button:
            await self.session.delete(button)
            await self.session.commit()
            return True
        return False


class LinkClickRepository(BaseRepository):
    async def record_click(self, link_id: int, user_id: int, chat_id: int | None) -> bool:
        """Atomic insert-or-ignore — returns True only for a user's FIRST
        click on this link, mirroring PromoRepository.use()'s pattern so a
        repeat tap never counts twice."""
        self.session.add(ChatLinkClick(link_id=link_id, user_id=user_id, chat_id=chat_id))
        try:
            await self.session.flush()
            await self.session.commit()
            return True
        except IntegrityError:
            await self.session.rollback()
            return False

    async def count_for_chat(self, chat_id: int) -> int:
        result = await self.session.execute(
            select(func.count(ChatLinkClick.id)).where(ChatLinkClick.chat_id == chat_id)
        )
        return int(result.scalar_one())
