import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BotSettings, ReferralReactivation, User
from bot.services.referral import (
    check_referral_reward,
    get_referral_reward,
    reward_returning_referral,
)


def _wave_json(*urls: str) -> str:
    import json
    return json.dumps([{"url": url, "provider": "tgrass", "name": "Sponsor"} for url in urls])


def _bot(confirmed_statuses: dict[str, str] | None = None) -> SimpleNamespace:
    """A fake Bot whose get_chat_member confirms membership per chat_id
    (keyed by the @username telegram_chat_id() resolves each t.me URL to),
    defaulting to a confirmed member for anything not listed."""
    statuses = confirmed_statuses or {}

    async def get_chat_member(chat_id, user_id):
        return SimpleNamespace(status=statuses.get(chat_id, "member"))

    return SimpleNamespace(get_chat_member=AsyncMock(side_effect=get_chat_member), send_message=AsyncMock())


class ReferralRewardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_referral_reward_rounds_to_stars_precision(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_reward", value="4.005"))
            await session.commit()
            reward = await get_referral_reward(session)
        self.assertEqual(reward, Decimal("4.01"))

    async def test_same_return_cycle_is_rewarded_only_once(self) -> None:
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            referrer = User(user_id=700, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=701,
                first_name="Referred",
                referrer_id=700,
                referral_counted=True,
                referral_reward_given=True,
                last_seen_at=inactive_since,
            )
            session.add_all((referrer, referred))
            await session.commit()

            first = await reward_returning_referral(
                referred, referrer.user_id, inactive_since, session,
            )
            second = await reward_returning_referral(
                referred, referrer.user_id, inactive_since, session,
            )

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 700)
            ledger_count = (
                await session.execute(select(func.count(ReferralReactivation.id)))
            ).scalar_one()

        # Half of the default flat referral_reward (4 -> 2.00).
        self.assertEqual(first, Decimal("2.00"))
        self.assertIsNone(second)
        self.assertEqual(float(saved_referrer.stars_balance), 2.0)
        self.assertEqual(ledger_count, 1)

    async def test_ordinary_referral_reward_is_paid_once_and_marks_counted(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=710, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=711,
                first_name="Referred",
                referrer_id=710,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(
                    "https://t.me/a", "https://t.me/b", "https://t.me/c"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)
            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 710)
            saved_referred = await session.get(User, 711)

        # Flat default reward (4⭐), paid exactly once regardless of how many
        # times check_referral_reward is called afterwards.
        self.assertEqual(float(saved_referrer.stars_balance), 4.0)
        self.assertEqual(saved_referrer.referrals_count, 1)
        self.assertTrue(saved_referred.referral_reward_given)
        self.assertTrue(saved_referred.referral_counted)

    async def test_insufficient_sponsors_notifies_once_then_pays_once_threshold_met(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=720, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=721,
                first_name="Referred",
                referrer_id=720,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json("https://t.me/a"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot()
            await check_referral_reward(referred, session, bot)
            await check_referral_reward(referred, session, bot)  # same single sponsor, still short

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 720)
            saved_referred = await session.get(User, 721)

        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertEqual(saved_referrer.referrals_count, 0)
        self.assertFalse(saved_referred.referral_reward_given)
        self.assertTrue(saved_referred.referral_insufficient_notified)
        bot.send_message.assert_awaited_once()

        # Once the referred user reaches the minimum (3) sponsors, the
        # payout goes through — a single flat reward, not multiplied by
        # sponsor count.
        async with self.sessions() as session:
            saved_referred = await session.get(User, 721)
            saved_referred.sponsor_wave_two = _wave_json("https://t.me/b", "https://t.me/c")
            await session.commit()
            await check_referral_reward(saved_referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 720)
            saved_referred = await session.get(User, 721)

        self.assertEqual(float(saved_referrer.stars_balance), 4.0)
        self.assertEqual(saved_referrer.referrals_count, 1)
        self.assertTrue(saved_referred.referral_reward_given)

    async def test_new_sponsors_after_payout_are_never_paid_again(self) -> None:
        """Reversion of the earlier 'recurring reward' behavior: once a
        referral has been rewarded, gaining new sponsors later must not
        trigger another payout."""
        async with self.sessions() as session:
            referrer = User(user_id=730, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=731,
                first_name="Referred",
                referrer_id=730,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(*[f"https://t.me/tg{i}" for i in range(3)]),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 730)
        self.assertEqual(float(saved_referrer.stars_balance), 4.0)
        self.assertEqual(saved_referrer.referrals_count, 1)

        # Simulate the recheck scheduler unlocking brand-new sponsors after
        # the reward has already been paid.
        async with self.sessions() as session:
            saved_referred = await session.get(User, 731)
            saved_referred.sponsor_wave_two = _wave_json("https://t.me/new1", "https://t.me/new2")
            await session.commit()
            await check_referral_reward(saved_referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 730)

        # No second payout, no second milestone increment.
        self.assertEqual(float(saved_referrer.stars_balance), 4.0)
        self.assertEqual(saved_referrer.referrals_count, 1)

    async def test_bot_side_verification_excludes_unconfirmed_tg_sponsor(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=740, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=741,
                first_name="Referred",
                referrer_id=740,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(
                    "https://t.me/good1", "https://t.me/good2", "https://t.me/good3", "https://t.me/fake"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot(confirmed_statuses={"@fake": "left"})
            await check_referral_reward(referred, session, bot)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 740)
            saved_referred = await session.get(User, 741)

        # 3 of the 4 sponsors are bot-confirmed, meeting the minimum (3) —
        # the flat reward is paid despite the unconfirmed 4th sponsor.
        self.assertEqual(float(saved_referrer.stars_balance), 4.0)
        self.assertTrue(saved_referred.referral_reward_given)

    async def test_unconfirmed_sponsor_can_drop_total_below_minimum(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=750, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=751,
                first_name="Referred",
                referrer_id=750,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(
                    "https://t.me/good1", "https://t.me/good2", "https://t.me/fake"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot(confirmed_statuses={"@fake": "left"})
            await check_referral_reward(referred, session, bot)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 750)
            saved_referred = await session.get(User, 751)

        # Only 2 of 3 sponsors are confirmed — below the minimum of 3 — so
        # no payout happens even though the bot never goes negative for it.
        self.assertEqual(float(saved_referrer.stars_balance), 0.0)
        self.assertFalse(saved_referred.referral_reward_given)

    async def test_milestone_bonus_and_vip_apply_on_top_of_flat_reward(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="referral_bonus_10", value="0.1"))
            await session.commit()
            referrer = User(
                user_id=760,
                first_name="Referrer",
                stars_balance=Decimal(0),
                referrals_count=9,
            )
            referred = User(
                user_id=761,
                first_name="Referred",
                referrer_id=760,
                sponsors_verified=True,
                sponsor_wave_one=_wave_json(
                    "https://t.me/a", "https://t.me/b", "https://t.me/c"
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 760)

        # Flat reward (4) + the 10-referral milestone bonus (0.1).
        self.assertEqual(float(saved_referrer.stars_balance), 4.1)
        self.assertEqual(saved_referrer.referrals_count, 10)


if __name__ == "__main__":
    unittest.main()
