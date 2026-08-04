import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.repositories.settings import SettingsRepository
from bot.services.house_edge import is_in_recovery


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class IsInRecoveryTests(ChatModelsTestCase):
    async def test_no_history_is_not_in_recovery(self) -> None:
        async with self.sessions() as session:
            self.assertFalse(await is_in_recovery(session, "wheel"))

    async def test_house_ahead_is_not_in_recovery(self) -> None:
        async with self.sessions() as session:
            repo = SettingsRepository(session)
            await repo.add_float("wheel_total_bet", 100.0)
            await repo.add_float("wheel_total_payout", 50.0)
            self.assertFalse(await is_in_recovery(session, "wheel"))

    async def test_house_net_negative_triggers_recovery(self) -> None:
        async with self.sessions() as session:
            repo = SettingsRepository(session)
            await repo.add_float("wheel_total_bet", 100.0)
            await repo.add_float("wheel_total_payout", 150.0)
            self.assertTrue(await is_in_recovery(session, "wheel"))

    async def test_payout_exactly_equal_to_bet_triggers_recovery(self) -> None:
        async with self.sessions() as session:
            repo = SettingsRepository(session)
            await repo.add_float("mines_total_bet", 80.0)
            await repo.add_float("mines_total_payout", 80.0)
            self.assertTrue(await is_in_recovery(session, "mines"))

    async def test_game_keys_are_isolated_from_each_other(self) -> None:
        async with self.sessions() as session:
            repo = SettingsRepository(session)
            await repo.add_float("roulette_total_bet", 100.0)
            await repo.add_float("roulette_total_payout", 200.0)
            self.assertTrue(await is_in_recovery(session, "roulette"))
            self.assertFalse(await is_in_recovery(session, "doors"))


if __name__ == "__main__":
    unittest.main()
