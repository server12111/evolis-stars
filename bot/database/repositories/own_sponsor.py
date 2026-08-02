from sqlalchemy import not_, or_, select, update
from sqlalchemy.exc import IntegrityError

from bot.database.models import OwnSponsor, OwnSponsorCompletion
from bot.database.repositories.base import BaseRepository


class OwnSponsorRepository(BaseRepository):
    async def all(self) -> list[OwnSponsor]:
        result = await self.session.execute(select(OwnSponsor).order_by(OwnSponsor.id.desc()))
        return list(result.scalars().all())

    async def all_active(self) -> list[OwnSponsor]:
        result = await self.session.execute(
            select(OwnSponsor).where(OwnSponsor.is_active == True).order_by(OwnSponsor.id)
        )
        return list(result.scalars().all())

    async def get(self, sponsor_id: int) -> OwnSponsor | None:
        return await self.session.get(OwnSponsor, sponsor_id)

    async def is_completed(self, sponsor_id: int, user_id: int) -> bool:
        result = await self.session.execute(
            select(OwnSponsorCompletion.id).where(
                OwnSponsorCompletion.sponsor_id == sponsor_id,
                OwnSponsorCompletion.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def completed_sponsor_ids(self, user_id: int) -> set[int]:
        result = await self.session.execute(
            select(OwnSponsorCompletion.sponsor_id).where(OwnSponsorCompletion.user_id == user_id)
        )
        return set(result.scalars().all())

    async def pending_for_user(self, user_id: int) -> list[OwnSponsor]:
        """Active sponsors this user has not yet completed."""
        completed_subquery = select(OwnSponsorCompletion.sponsor_id).where(
            OwnSponsorCompletion.user_id == user_id
        )
        result = await self.session.execute(
            select(OwnSponsor)
            .where(
                OwnSponsor.is_active == True,
                not_(OwnSponsor.id.in_(completed_subquery)),
            )
            .order_by(OwnSponsor.id)
        )
        return list(result.scalars().all())

    async def mark_completed(self, sponsor_id: int, user_id: int) -> bool:
        if await self.is_completed(sponsor_id, user_id):
            return False
        result = await self.session.execute(
            update(OwnSponsor)
            .where(
                OwnSponsor.id == sponsor_id,
                or_(OwnSponsor.current_count < OwnSponsor.target_count, OwnSponsor.target_count <= 0),
            )
            .values(current_count=OwnSponsor.current_count + 1)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False

        self.session.add(OwnSponsorCompletion(sponsor_id=sponsor_id, user_id=user_id))
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return False

        sponsor = await self.session.get(OwnSponsor, sponsor_id)
        if sponsor and sponsor.target_count > 0 and sponsor.current_count >= sponsor.target_count:
            sponsor.is_active = False
        await self.session.commit()
        return True

    async def create(
        self,
        kind: str,
        url: str,
        name: str,
        target_count: int,
        target: str | None = None,
    ) -> OwnSponsor:
        sponsor = OwnSponsor(
            kind=kind,
            target=target,
            url=url,
            name=name,
            target_count=target_count,
        )
        self.session.add(sponsor)
        await self.session.commit()
        return sponsor

    async def toggle(self, sponsor_id: int) -> OwnSponsor | None:
        sponsor = await self.session.get(OwnSponsor, sponsor_id)
        if not sponsor:
            return None
        sponsor.is_active = not sponsor.is_active
        await self.session.commit()
        return sponsor

    async def delete(self, sponsor_id: int) -> bool:
        sponsor = await self.session.get(OwnSponsor, sponsor_id)
        if sponsor:
            await self.session.delete(sponsor)
            await self.session.commit()
            return True
        return False
