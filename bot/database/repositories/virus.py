from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bot.database.models import VirusInfection
from bot.database.repositories.base import BaseRepository


class VirusInfectionRepository(BaseRepository):
    async def get(self, infected_user_id: int) -> VirusInfection | None:
        return await self.session.get(VirusInfection, infected_user_id)

    async def create(self, infected_user_id: int, infector_user_id: int, virus_type: str) -> VirusInfection | None:
        """Returns None (instead of raising) if this user is already
        infected -- infected_user_id is the table's primary key, so a
        concurrent double-infect attempt loses the IntegrityError race
        instead of creating a second active infection."""
        infection = VirusInfection(
            infected_user_id=infected_user_id,
            infector_user_id=infector_user_id,
            virus_type=virus_type,
        )
        self.session.add(infection)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return None
        await self.session.commit()
        return infection

    async def cure(self, infected_user_id: int) -> bool:
        """Atomically claims and removes the infection. Returns False if it
        was already cured/never existed -- callers must not treat that as
        a fresh cure (e.g. don't re-announce it)."""
        result = await self.session.execute(
            sa_delete(VirusInfection).where(VirusInfection.infected_user_id == infected_user_id)
        )
        await self.session.commit()
        return result.rowcount == 1

    async def list_active(self) -> list[VirusInfection]:
        result = await self.session.execute(select(VirusInfection))
        return list(result.scalars().all())

    async def advance_payout(self, infected_user_id: int, expected_last_payout_at: datetime, new_last_payout_at: datetime) -> bool:
        """Optimistic advance -- only succeeds if last_payout_at still
        matches what the caller last read, so a cure racing with a
        scheduler tick can never have the tick "resurrect" last_payout_at
        on a row that's meanwhile been deleted, nor double-advance it."""
        result = await self.session.execute(
            update(VirusInfection)
            .where(
                VirusInfection.infected_user_id == infected_user_id,
                VirusInfection.last_payout_at == expected_last_payout_at,
            )
            .values(last_payout_at=new_last_payout_at)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False
        await self.session.commit()
        return True
