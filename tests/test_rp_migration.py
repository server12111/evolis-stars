import unittest
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base, _add_missing_user_columns
from bot.database.models import BotSettings, ChatBonusCode, PromoCode, User


class RpMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _run_migration(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(_add_missing_user_columns)

    async def test_balance_and_credit_are_tripled_exactly_once(self) -> None:
        async with self.sessions() as session:
            session.add(User(
                user_id=1, first_name="A", stars_balance=Decimal("10"),
                free_game_credit_amount=Decimal("2"),
            ))
            await session.commit()
        # Simulate a pre-existing row from before the RP⭐️ migration shipped
        # (the model's default=True only applies to rows created via the
        # ORM after this feature existed).
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE users SET rp_migrated = 0 WHERE user_id = 1"))

        await self._run_migration()

        async with self.sessions() as session:
            user = await session.get(User, 1)
        self.assertEqual(user.stars_balance, Decimal("30.00"))
        self.assertEqual(user.free_game_credit_amount, Decimal("6.00"))
        self.assertTrue(user.rp_migrated)

        # Restart (or a second call in the same process) must not re-multiply.
        await self._run_migration()
        async with self.sessions() as session:
            user = await session.get(User, 1)
        self.assertEqual(user.stars_balance, Decimal("30.00"))

    async def test_new_user_created_after_migration_is_never_touched(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=2, first_name="B", stars_balance=Decimal("10")))
            await session.commit()
        # rp_migrated defaults to True for a freshly-created row — the
        # migration's WHERE clause must skip it.
        await self._run_migration()
        async with self.sessions() as session:
            user = await session.get(User, 2)
        self.assertEqual(user.stars_balance, Decimal("10.00"))

    async def test_null_credit_amount_stays_null_not_zero(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=3, first_name="C", stars_balance=Decimal("5"), free_game_credit_amount=None))
            await session.commit()
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE users SET rp_migrated = 0 WHERE user_id = 3"))
        await self._run_migration()
        async with self.sessions() as session:
            user = await session.get(User, 3)
        self.assertIsNone(user.free_game_credit_amount)

    async def test_migration_count_accumulates_once(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=4, first_name="D", stars_balance=Decimal("1")))
            session.add(User(user_id=5, first_name="E", stars_balance=Decimal("1")))
            await session.commit()
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE users SET rp_migrated = 0 WHERE user_id IN (4, 5)"))

        await self._run_migration()
        async with self.sessions() as session:
            row = await session.get(BotSettings, "rp_migration_count")
        self.assertEqual(row.value, "2")

        # A second run (nothing new to migrate) must not bump the counter.
        await self._run_migration()
        async with self.sessions() as session:
            row = await session.get(BotSettings, "rp_migration_count")
        self.assertEqual(row.value, "2")

    async def test_chat_bonus_and_promo_reward_amounts_tripled_once_usage_limit_untouched(self) -> None:
        async with self.sessions() as session:
            session.add(ChatBonusCode(
                chat_id=-1, code="X", reward_amount=Decimal("0.5"), usage_limit=10,
                total_charged=Decimal("5.35"), created_by=1,
            ))
            session.add(PromoCode(code="Y", reward_amount=Decimal("2"), usage_limit=5))
            await session.commit()

        await self._run_migration()
        async with self.sessions() as session:
            bonus = (await session.execute(ChatBonusCode.__table__.select())).first()
            promo = (await session.execute(PromoCode.__table__.select())).first()
        self.assertEqual(bonus.reward_amount, Decimal("1.50"))
        self.assertEqual(bonus.total_charged, Decimal("16.05"))
        self.assertEqual(bonus.usage_limit, 10)
        self.assertEqual(promo.reward_amount, Decimal("6.00"))
        self.assertEqual(promo.usage_limit, 5)

        # Re-running must not triple again.
        await self._run_migration()
        async with self.sessions() as session:
            bonus = (await session.execute(ChatBonusCode.__table__.select())).first()
        self.assertEqual(bonus.reward_amount, Decimal("1.50"))


if __name__ == "__main__":
    unittest.main()
