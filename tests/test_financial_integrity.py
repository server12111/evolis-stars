import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import (
    AuctionBid,
    AuctionRound,
    BotSettings,
    Duel,
    Lottery,
    LotteryTicket,
    PromoCode,
    PromoUse,
    Task,
    TaskCompletion,
    User,
    Withdrawal,
)
from bot.database.repositories.auction import AuctionRepository
from bot.database.repositories.lottery import LotteryRepository
from bot.database.repositories.promo import PromoRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.task import TaskRepository
from bot.database.repositories.withdrawal import WithdrawalRepository
from bot.handlers.duel import (
    _duel_rules,
    cb_duel_confirm_join,
    cb_duel_decline_join,
)
from bot.services.duel_scheduler import _settle_expired_duel


class FinancialIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_duel_timeout_records_the_only_player_who_rolled(self) -> None:
        async with self.sessions() as session:
            session.add_all(
                (
                    User(user_id=10, first_name="Creator"),
                    User(user_id=11, first_name="Joiner"),
                )
            )
            duel = Duel(
                creator_id=10,
                joiner_id=11,
                amount=Decimal("5"),
                status="active",
                creator_roll=6,
                expires_at=datetime.utcnow() - timedelta(seconds=1),
            )
            session.add(duel)
            await session.commit()
            duel_id = duel.id

        bot = AsyncMock()
        with patch("bot.services.duel_scheduler.SessionFactory", self.sessions):
            await _settle_expired_duel(SimpleNamespace(id=duel_id), bot)

        async with self.sessions() as session:
            saved_duel = await session.get(Duel, duel_id)
            creator = await session.get(User, 10)
            joiner = await session.get(User, 11)

        self.assertEqual(saved_duel.status, "finished")
        self.assertEqual(saved_duel.winner_id, 10)
        self.assertEqual(float(creator.stars_balance), 5.0)
        self.assertEqual(float(joiner.stars_balance), 0.0)

    async def test_promo_use_and_reward_can_commit_together(self) -> None:
        async with self.sessions() as session:
            user = User(user_id=1, first_name="User")
            promo = PromoCode(
                code="TEST",
                reward_amount=Decimal(3),
                expires_at=datetime.utcnow() + timedelta(days=1),
            )
            session.add_all((user, promo))
            await session.commit()

            self.assertTrue(await PromoRepository(session).use(promo, user.user_id))
            user.stars_balance += promo.reward_amount
            await session.commit()

        async with self.sessions() as session:
            saved_user = await session.get(User, 1)
            saved_promo = (
                await session.execute(select(PromoCode).where(PromoCode.code == "TEST"))
            ).scalar_one()
            uses = (
                await session.execute(select(func.count(PromoUse.id)))
            ).scalar_one()
            self.assertEqual(float(saved_user.stars_balance), 3.0)
            self.assertEqual(saved_promo.used_count, 1)
            self.assertEqual(uses, 1)

    async def test_promo_use_rolls_back_if_reward_is_not_committed(self) -> None:
        async with self.sessions() as session:
            user = User(user_id=2, first_name="User")
            promo = PromoCode(code="ROLLBACK", reward_amount=Decimal(3))
            session.add_all((user, promo))
            await session.commit()
            self.assertTrue(await PromoRepository(session).use(promo, user.user_id))
            await session.rollback()

        async with self.sessions() as session:
            saved_promo = (
                await session.execute(
                    select(PromoCode).where(PromoCode.code == "ROLLBACK")
                )
            ).scalar_one()
            uses = (
                await session.execute(select(func.count(PromoUse.id)))
            ).scalar_one()
            self.assertEqual(saved_promo.used_count, 0)
            self.assertEqual(uses, 0)

    async def test_task_completion_and_reward_can_commit_together(self) -> None:
        async with self.sessions() as session:
            user = User(user_id=3, first_name="User")
            task = Task(
                title="Task",
                description="",
                url="",
                task_type="manual",
                reward=Decimal("0.3"),
            )
            session.add_all((user, task))
            await session.commit()

            self.assertTrue(
                await TaskRepository(session).mark_completed(task.id, user.user_id)
            )
            user.stars_balance += task.reward
            user.tasks_completed_count += 1
            await session.commit()

        async with self.sessions() as session:
            saved_user = await session.get(User, 3)
            completions = (
                await session.execute(select(func.count(TaskCompletion.id)))
            ).scalar_one()
            self.assertEqual(float(saved_user.stars_balance), 0.3)
            self.assertEqual(saved_user.tasks_completed_count, 1)
            self.assertEqual(completions, 1)

    async def test_rejection_and_refund_commit_together(self) -> None:
        async with self.sessions() as session:
            user = User(user_id=4, first_name="User", stars_balance=Decimal(0))
            withdrawal = Withdrawal(
                user_id=4,
                amount=Decimal(15),
                status="pending",
            )
            session.add_all((user, withdrawal))
            await session.commit()

            rejected = await WithdrawalRepository(session).reject(withdrawal.id)
            self.assertIsNotNone(rejected)
            user.stars_balance += rejected.amount
            await session.commit()

        async with self.sessions() as session:
            saved_user = await session.get(User, 4)
            saved_withdrawal = await session.get(Withdrawal, withdrawal.id)
            self.assertEqual(float(saved_user.stars_balance), 15.0)
            self.assertEqual(saved_withdrawal.status, "rejected")

    async def test_stale_auction_bid_cannot_overwrite_newer_bid(self) -> None:
        async with self.sessions() as session:
            session.add_all(
                (
                    User(user_id=10, first_name="One"),
                    User(user_id=11, first_name="Two"),
                )
            )
            round_ = AuctionRound(
                start_at=datetime.utcnow(),
                end_at=datetime.utcnow() + timedelta(hours=1),
            )
            session.add(round_)
            await session.commit()
            round_id = round_.id

        async with self.sessions() as session:
            stale = await session.get(AuctionRound, round_id)

        async with self.sessions() as session:
            current = await session.get(AuctionRound, round_id)
            self.assertTrue(
                await AuctionRepository(session).place_bid(
                    current,
                    10,
                    Decimal(1),
                    Decimal(1),
                )
            )

        async with self.sessions() as session:
            self.assertFalse(
                await AuctionRepository(session).place_bid(
                    stale,
                    11,
                    Decimal(1),
                    Decimal(1),
                )
            )

        async with self.sessions() as session:
            saved = await session.get(AuctionRound, round_id)
            bids = (
                await session.execute(select(func.count(AuctionBid.id)))
            ).scalar_one()
            self.assertEqual(saved.current_bidder_id, 10)
            self.assertEqual(float(saved.prize_pool), 1.0)
            self.assertEqual(bids, 1)

    async def test_stale_lottery_purchase_cannot_lose_counters(self) -> None:
        async with self.sessions() as session:
            session.add_all(
                (
                    User(user_id=20, first_name="One"),
                    User(user_id=21, first_name="Two"),
                )
            )
            lottery = Lottery(ticket_price=Decimal(5), end_value=10)
            session.add(lottery)
            await session.commit()
            lottery_id = lottery.id

        async with self.sessions() as session:
            stale = await session.get(Lottery, lottery_id)

        async with self.sessions() as session:
            current = await session.get(Lottery, lottery_id)
            self.assertTrue(
                await LotteryRepository(session).buy_ticket(current, 20)
            )

        async with self.sessions() as session:
            self.assertFalse(
                await LotteryRepository(session).buy_ticket(stale, 21)
            )

        async with self.sessions() as session:
            saved = await session.get(Lottery, lottery_id)
            tickets = (
                await session.execute(select(func.count(LotteryTicket.id)))
            ).scalar_one()
            self.assertEqual(saved.tickets_sold, 1)
            self.assertEqual(float(saved.total_collected), 5.0)
            self.assertEqual(float(saved.prize_pool), 3.5)
            self.assertEqual(tickets, 1)

    async def test_stale_lottery_cancel_cannot_burn_sold_ticket(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=22, first_name="Buyer"))
            lottery = Lottery(ticket_price=Decimal(5), end_value=10)
            session.add(lottery)
            await session.commit()
            lottery_id = lottery.id

        async with self.sessions() as stale_session:
            stale = await stale_session.get(Lottery, lottery_id)

        async with self.sessions() as current_session:
            current = await current_session.get(Lottery, lottery_id)
            self.assertTrue(
                await LotteryRepository(current_session).buy_ticket(current, 22)
            )

        async with self.sessions() as session:
            self.assertFalse(
                await LotteryRepository(session).cancel(stale)
            )

        async with self.sessions() as session:
            saved = await session.get(Lottery, lottery_id)
            self.assertEqual(saved.status, "active")
            self.assertEqual(saved.tickets_sold, 1)

    async def test_stale_duel_confirmation_cannot_revive_cancelled_duel(self) -> None:
        async with self.sessions() as session:
            creator = User(user_id=30, first_name="Creator", stars_balance=Decimal(0))
            joiner = User(user_id=31, first_name="Joiner", stars_balance=Decimal(0))
            duel = Duel(
                creator_id=creator.user_id,
                joiner_id=joiner.user_id,
                amount=Decimal(5),
                status="confirming",
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
            session.add_all((creator, joiner, duel))
            await session.commit()
            duel_id = duel.id

        async with self.sessions() as stale_session:
            await stale_session.get(Duel, duel_id)
            async with self.sessions() as current_session:
                await current_session.execute(
                    update(Duel)
                    .where(Duel.id == duel_id)
                    .values(status="cancelled")
                )
                await current_session.commit()

            callback = SimpleNamespace(
                data=f"duel:confirm:{duel_id}",
                answer=AsyncMock(),
            )
            await cb_duel_confirm_join(
                callback,
                stale_session,
                SimpleNamespace(user_id=30),
            )

        async with self.sessions() as session:
            saved = await session.get(Duel, duel_id)
            self.assertEqual(saved.status, "cancelled")
        callback.answer.assert_awaited_once()

    async def test_stale_duel_decline_cannot_refund_twice(self) -> None:
        async with self.sessions() as session:
            creator = User(user_id=40, first_name="Creator", stars_balance=Decimal(5))
            joiner = User(user_id=41, first_name="Joiner", stars_balance=Decimal(5))
            duel = Duel(
                creator_id=creator.user_id,
                joiner_id=joiner.user_id,
                amount=Decimal(5),
                status="confirming",
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
            session.add_all((creator, joiner, duel))
            await session.commit()
            duel_id = duel.id

        async with self.sessions() as stale_session:
            await stale_session.get(Duel, duel_id)
            async with self.sessions() as current_session:
                await current_session.execute(
                    update(Duel)
                    .where(Duel.id == duel_id)
                    .values(status="cancelled")
                )
                await current_session.commit()

            callback = SimpleNamespace(
                data=f"duel:decline_join:{duel_id}",
                answer=AsyncMock(),
            )
            await cb_duel_decline_join(
                callback,
                stale_session,
                SimpleNamespace(user_id=40),
            )

        async with self.sessions() as session:
            creator = await session.get(User, 40)
            joiner = await session.get(User, 41)
            saved = await session.get(Duel, duel_id)
            self.assertEqual(saved.status, "cancelled")
            self.assertEqual(float(creator.stars_balance), 5.0)
            self.assertEqual(float(joiner.stars_balance), 5.0)
        callback.answer.assert_awaited_once()

    async def test_duel_rules_use_admin_settings_as_percent(self) -> None:
        async with self.sessions() as session:
            session.add_all(
                (
                    BotSettings(key="duel_min_refs", value="7"),
                    BotSettings(key="duel_commission", value="25"),
                )
            )
            await session.commit()
            min_refs, commission = await _duel_rules(session)

        self.assertEqual(min_refs, 7)
        self.assertEqual(commission, 0.25)

    async def test_global_game_stats_are_incremented_in_sql(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="mines_total_bet", value="10.25"))
            await session.commit()
            repository = SettingsRepository(session)
            await repository.add_float("mines_total_bet", 1.125)
            await repository.add_float("mines_total_bet", 2.5)
            await session.commit()

        async with self.sessions() as session:
            saved = await session.get(BotSettings, "mines_total_bet")
            self.assertAlmostEqual(float(saved.value), 13.875, places=4)


if __name__ == "__main__":
    unittest.main()
