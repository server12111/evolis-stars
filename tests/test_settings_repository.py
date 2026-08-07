import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.repositories.settings import SettingsRepository


class GetPrefixedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_returns_only_matching_prefix_as_a_dict(self) -> None:
        async with self.sessions() as session:
            repo = SettingsRepository(session)
            await repo.set("task_skipped:1:10", "111")
            await repo.set("task_skipped:1:20", "222")
            await repo.set("task_skipped:2:10", "333")  # different user -- must not leak in
            await repo.set("pf_skipped:1:abc", "444")  # different prefix entirely

            result = await repo.get_prefixed("task_skipped:1:")

        self.assertEqual(
            result,
            {"task_skipped:1:10": "111", "task_skipped:1:20": "222"},
        )

    async def test_empty_when_nothing_matches(self) -> None:
        async with self.sessions() as session:
            repo = SettingsRepository(session)
            await repo.set("unrelated_key", "1")
            result = await repo.get_prefixed("task_skipped:1:")

        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
