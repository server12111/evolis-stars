from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.database.models import ChatWallIntegrationCompletion
from bot.database.repositories.base import BaseRepository


class ChatWallIntegrationRepository(BaseRepository):
    async def completed_pairs(self, user_id: int) -> set[tuple[str, str]]:
        """(provider, item_key) pairs this user has already been credited
        for -- one query, used to filter a frozen batch down to what's
        still pending without a provider call (the gate's hot path)."""
        result = await self.session.execute(
            select(ChatWallIntegrationCompletion.provider, ChatWallIntegrationCompletion.item_key)
            .where(ChatWallIntegrationCompletion.user_id == user_id)
        )
        return set(result.all())

    async def is_completed(self, user_id: int, provider: str, item_key: str) -> bool:
        result = await self.session.execute(
            select(ChatWallIntegrationCompletion.id).where(
                ChatWallIntegrationCompletion.user_id == user_id,
                ChatWallIntegrationCompletion.provider == provider,
                ChatWallIntegrationCompletion.item_key == item_key,
            )
        )
        return result.scalar_one_or_none() is not None

    async def mark_completed(self, user_id: int, provider: str, item_key: str) -> bool:
        """Race-safe: the unique constraint on (user_id, provider,
        item_key) is the actual guard against a double credit, not the
        is_completed() probe above (a cheap fast-path to skip the
        insert/rollback round-trip in the common repeat-check case)."""
        if await self.is_completed(user_id, provider, item_key):
            return False
        self.session.add(
            ChatWallIntegrationCompletion(user_id=user_id, provider=provider, item_key=item_key)
        )
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return False
        await self.session.commit()
        return True
