from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.database.models import BlockedSponsorUrl
from bot.database.repositories.base import BaseRepository
from bot.services.sponsor_waves import normalize_sponsor_url


class BlockedSponsorRepository(BaseRepository):
    async def all(self) -> list[BlockedSponsorUrl]:
        result = await self.session.execute(
            select(BlockedSponsorUrl).order_by(BlockedSponsorUrl.id.desc())
        )
        return list(result.scalars().all())

    async def get(self, entry_id: int) -> BlockedSponsorUrl | None:
        return await self.session.get(BlockedSponsorUrl, entry_id)

    async def url_key_set(self) -> set[str]:
        result = await self.session.execute(select(BlockedSponsorUrl.url_key))
        return set(result.scalars().all())

    async def create(self, url: str) -> BlockedSponsorUrl:
        """Idempotent: pasting an already-blocked URL again just returns
        the existing row instead of erroring or duplicating it."""
        url_key = normalize_sponsor_url(url)
        existing = await self.session.execute(
            select(BlockedSponsorUrl).where(BlockedSponsorUrl.url_key == url_key)
        )
        found = existing.scalar_one_or_none()
        if found:
            return found

        entry = BlockedSponsorUrl(url=url, url_key=url_key)
        self.session.add(entry)
        try:
            await self.session.commit()
        except IntegrityError:
            # Lost a create/create race -- the other write already exists.
            await self.session.rollback()
            existing = await self.session.execute(
                select(BlockedSponsorUrl).where(BlockedSponsorUrl.url_key == url_key)
            )
            return existing.scalar_one()
        return entry

    async def delete(self, entry_id: int) -> bool:
        entry = await self.session.get(BlockedSponsorUrl, entry_id)
        if entry:
            await self.session.delete(entry)
            await self.session.commit()
            return True
        return False
