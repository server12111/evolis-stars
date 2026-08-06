import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.services.withdrawal_numbering import next_withdrawal_number


class NextWithdrawalNumberTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_first_call_seeds_and_returns_one(self) -> None:
        async with self.sessions() as session:
            number = await next_withdrawal_number(session)
            await session.commit()
        self.assertEqual(number, 1)

    async def test_sequence_is_shared_across_unrelated_callers(self) -> None:
        """Stars and VC withdrawals both call this -- must be ONE continuous
        sequence, not two independent counters that collide once both
        currencies have issued the same number of requests."""
        async with self.sessions() as session:
            first = await next_withdrawal_number(session)
            second = await next_withdrawal_number(session)
            third = await next_withdrawal_number(session)
            await session.commit()
        self.assertEqual([first, second, third], [1, 2, 3])

    async def test_rolled_back_allocation_is_not_lost_or_duplicated(self) -> None:
        async with self.sessions() as session:
            await next_withdrawal_number(session)
            await session.commit()

        async with self.sessions() as session:
            await next_withdrawal_number(session)
            await session.rollback()  # e.g. the admin-channel send failed

        async with self.sessions() as session:
            number = await next_withdrawal_number(session)
            await session.commit()
        self.assertEqual(number, 2)  # the rolled-back "2" is reissued, not skipped


if __name__ == "__main__":
    unittest.main()
