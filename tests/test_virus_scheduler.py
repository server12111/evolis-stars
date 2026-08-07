import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User, VirusInfection
from bot.database.repositories.virus import VirusInfectionRepository
from bot.services.virus_scheduler import _pay_out_tick


class VirusIncomeSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        # Patch the module-level SessionFactory the scheduler imports so it
        # shares this test's in-memory engine instead of the real one.
        patcher = patch("bot.services.virus_scheduler.SessionFactory", self.sessions)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed(self, infected_id: int, infector_id: int, virus_type: str, last_payout_at: datetime) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=infected_id, first_name="I", stars_balance=Decimal("0")))
            session.add(User(user_id=infector_id, first_name="O", stars_balance=Decimal("0")))
            session.add(VirusInfection(
                infected_user_id=infected_id, infector_user_id=infector_id,
                virus_type=virus_type, last_payout_at=last_payout_at,
            ))
            await session.commit()

    async def test_less_than_one_hour_elapsed_pays_nothing(self) -> None:
        await self._seed(1, 2, "light", datetime.utcnow() - timedelta(minutes=30))
        await _pay_out_tick()
        async with self.sessions() as session:
            owner = await session.get(User, 2)
        self.assertEqual(owner.stars_balance, Decimal("0"))

    async def test_one_hour_elapsed_pays_hourly_rate_once(self) -> None:
        await self._seed(1, 2, "light", datetime.utcnow() - timedelta(hours=1, minutes=1))
        await _pay_out_tick()
        async with self.sessions() as session:
            owner = await session.get(User, 2)
            infection = await VirusInfectionRepository(session).get(1)
        self.assertEqual(owner.stars_balance, Decimal("0.05"))
        # last_payout_at advanced by exactly the paid hour, not reset to now
        # -- the leftover minute stays owed for the next tick.
        self.assertLess(datetime.utcnow() - infection.last_payout_at, timedelta(minutes=2))

    async def test_catch_up_pays_multiple_elapsed_hours_at_once(self) -> None:
        await self._seed(1, 2, "dangerous", datetime.utcnow() - timedelta(hours=5, minutes=30))
        await _pay_out_tick()
        async with self.sessions() as session:
            owner = await session.get(User, 2)
        self.assertEqual(owner.stars_balance, Decimal("2.00"))  # 0.40 * 5

    async def test_cured_infection_is_not_paid(self) -> None:
        await self._seed(1, 2, "light", datetime.utcnow() - timedelta(hours=2))
        async with self.sessions() as session:
            await VirusInfectionRepository(session).cure(1)
        await _pay_out_tick()
        async with self.sessions() as session:
            owner = await session.get(User, 2)
        self.assertEqual(owner.stars_balance, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
