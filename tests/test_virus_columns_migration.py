import unittest

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base, _add_missing_user_columns
from bot.database.models import User


class LegacyVirusColumnsMigrationTests(unittest.IsolatedAsyncioTestCase):
    """A production DB that predates the "Вирус" game has no
    virus_last_used_at/virus_bonus_attempt columns on users at all --
    the migration must backfill safe defaults (no cooldown yet, no bonus
    attempt) rather than crash or leave existing rows unreadable."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            # Roll just the two new columns back off an otherwise-current
            # schema -- every other column a real legacy DB would already
            # have from earlier migrations stays untouched.
            await connection.execute(text("ALTER TABLE users DROP COLUMN virus_last_used_at"))
            await connection.execute(text("ALTER TABLE users DROP COLUMN virus_bonus_attempt"))
            await connection.execute(text(
                "INSERT INTO users ("
                "user_id, first_name, stars_balance, rp_migrated, referrals_count, "
                "referral_reward_given, referral_counted, referral_insufficient_notified, "
                "sponsors_verified, tos_accepted, tos_gate_shown, sponsor_wave, "
                "wall_integration_wave, "
                "tasks_completed_count, is_blocked, is_admin, is_vip, is_premium, "
                "free_game_credits, slots_777_count, darts_bullseye_count"
                ") VALUES (1, 'Legacy', 10, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)"
            ))
            await connection.run_sync(_add_missing_user_columns)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_existing_row_backfilled_with_safe_defaults(self) -> None:
        async with self.sessions() as session:
            rows = (await session.execute(select(User))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].virus_last_used_at)
        self.assertFalse(rows[0].virus_bonus_attempt)

    async def test_migration_is_idempotent_on_repeated_startup(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(_add_missing_user_columns)
        async with self.sessions() as session:
            rows = (await session.execute(select(User))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].virus_last_used_at)


if __name__ == "__main__":
    unittest.main()
