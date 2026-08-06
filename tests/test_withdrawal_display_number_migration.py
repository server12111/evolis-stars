import unittest

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base, _add_missing_user_columns
from bot.database.models import VcWithdrawal, Withdrawal
from bot.services.withdrawal_numbering import next_withdrawal_number


class LegacyWithdrawalNumberingMigrationTests(unittest.IsolatedAsyncioTestCase):
    """A production DB that predates display_number/withdrawal_counters --
    exactly the state reported live: a fresh VC withdrawal came out "#1",
    colliding with an old Stars withdrawal already posted as "#1" in the
    same channel, because each table's own autoincrement id was shown
    as-is with no shared sequence. The migration must backfill existing
    rows to their own `id` (what admins already saw for them) and seed the
    shared counter above the highest id from EITHER currency, so the next
    newly-issued number can never collide with something already posted."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            # Roll both tables back to their pre-display_number shape and
            # seed them with rows as if this were a live install.
            await connection.execute(text("DROP TABLE withdrawals"))
            await connection.execute(text(
                "CREATE TABLE withdrawals ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id BIGINT NOT NULL, "
                "amount NUMERIC(14,2) NOT NULL, "
                "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
                "channel_message_id INTEGER, admin_message_id INTEGER, "
                "created_at DATETIME, processed_at DATETIME, "
                "recipient_username VARCHAR(64), withdrawal_method VARCHAR(16), "
                "rp_debited NUMERIC(14,2))"
            ))
            for _ in range(3):
                await connection.execute(text(
                    "INSERT INTO withdrawals (user_id, amount, recipient_username) "
                    "VALUES (1, 15, 'tester')"
                ))  # ids 1, 2, 3

            await connection.execute(text("DROP TABLE vc_withdrawals"))
            await connection.execute(text(
                "CREATE TABLE vc_withdrawals ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id BIGINT NOT NULL, "
                "vc_amount NUMERIC(14,2) NOT NULL, "
                "rate NUMERIC(10,2) NOT NULL, "
                "rp_debited NUMERIC(14,2) NOT NULL, "
                "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
                "admin_message_id INTEGER, created_at DATETIME, processed_at DATETIME)"
            ))
            await connection.execute(text(
                "INSERT INTO vc_withdrawals (user_id, vc_amount, rate, rp_debited) "
                "VALUES (1, 10000, 1000, 10)"
            ))  # id 1 -- the exact "заявка #1 collides with an old stars
                # #1" scenario reported live.

            await connection.run_sync(_add_missing_user_columns)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_existing_rows_backfilled_to_their_own_id(self) -> None:
        async with self.sessions() as session:
            stars = (await session.execute(select(Withdrawal))).scalars().all()
            vc = (await session.execute(select(VcWithdrawal))).scalars().all()
        self.assertEqual(sorted(w.display_number for w in stars), [1, 2, 3])
        self.assertEqual(vc[0].display_number, 1)

    async def test_counter_seeded_above_the_highest_id_from_either_table(self) -> None:
        async with self.sessions() as session:
            number = await next_withdrawal_number(session)
            await session.commit()
        self.assertEqual(number, 4)  # highest pre-existing id was 3 (stars)

    async def test_migration_is_idempotent_on_repeated_startup(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(_add_missing_user_columns)
        async with self.sessions() as session:
            number = await next_withdrawal_number(session)
            await session.commit()
        # Counter must still be seeded from the original backfill (4, 5,
        # ...), not re-seeded back down to a lower value on a second run.
        self.assertEqual(number, 4)


if __name__ == "__main__":
    unittest.main()
