import json
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
    get_tg_reward,
    get_web_reward,
    reward_returning_referral,
)


def _wave(*urls: str) -> str:
    return json.dumps([{"url": url, "provider": "tgrass", "name": "Sponsor"} for url in urls])


def _bot(confirmed_statuses: dict[str, str] | None = None) -> SimpleNamespace:
    """A fake Bot whose get_chat_member confirms membership per chat_id
    (keyed by the @username telegram_chat_id() resolves each t.me URL to),
    defaulting to a confirmed member for anything not listed."""
    statuses = confirmed_statuses or {}

    async def get_chat_member(chat_id, user_id):
        return SimpleNamespace(status=statuses.get(chat_id, "member"))

    return SimpleNamespace(get_chat_member=AsyncMock(side_effect=get_chat_member), send_message=AsyncMock())


class ReferralReactivationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_tg_and_web_rewards_round_to_stars_precision(self) -> None:
        async with self.sessions() as session:
            session.add(BotSettings(key="tg_sponsor_reward", value="0.503"))
            session.add(BotSettings(key="web_sponsor_reward", value="0.255"))
            await session.commit()
            tg_reward = await get_tg_reward(session)
            web_reward = await get_web_reward(session)
        self.assertEqual(tg_reward, Decimal("0.50"))
        self.assertEqual(web_reward, Decimal("0.26"))

    async def test_same_return_cycle_is_rewarded_only_once(self) -> None:
        inactive_since = datetime.utcnow() - timedelta(days=8)
        async with self.sessions() as session:
            referrer = User(
                user_id=700,
                first_name="Referrer",
                stars_balance=Decimal(0),
            )
            referred = User(
                user_id=701,
                first_name="Referred",
                referrer_id=700,
                referral_counted=True,
                referral_reward_given=True,
                sponsor_wave_one=_wave(
                    "https://t.me/one", "https://t.me/two"
                ),
                last_seen_at=inactive_since,
            )
            session.add_all((referrer, referred))
            await session.commit()

            first = await reward_returning_referral(
                referred,
                referrer.user_id,
                inactive_since,
                session,
            )
            second = await reward_returning_referral(
                referred,
                referrer.user_id,
                inactive_since,
                session,
            )

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 700)
            ledger_count = (
                await session.execute(
                    select(func.count(ReferralReactivation.id))
                )
            ).scalar_one()

        # 2 TG sponsors * 0.5 default reward, halved for a reactivation.
        self.assertEqual(first, Decimal("0.50"))
        self.assertIsNone(second)
        self.assertEqual(float(saved_referrer.stars_balance), 0.5)
        self.assertEqual(ledger_count, 1)

    async def test_ordinary_referral_reward_is_paid_once_and_marks_counted(self) -> None:
        async with self.sessions() as session:
            referrer = User(
                user_id=710,
                first_name="Referrer",
                stars_balance=Decimal(0),
            )
            referred = User(
                user_id=711,
                first_name="Referred",
                referrer_id=710,
                sponsors_verified=True,
                sponsor_wave_one=_wave(
                    "https://t.me/a",
                    "https://t.me/b",
                    "https://t.me/c",
                    "https://t.me/d",
                    "https://t.me/e",
                    "https://t.me/f",
                ),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)
            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 710)
            saved_referred = await session.get(User, 711)

        # 6 TG sponsors * 0.5 default reward. Second call has no new
        # sponsors beyond the already-rewarded set, so it's a no-op.
        self.assertEqual(float(saved_referrer.stars_balance), 3.0)
        self.assertEqual(saved_referrer.referrals_count, 1)
        self.assertTrue(saved_referred.referral_reward_given)
        self.assertTrue(saved_referred.referral_counted)

    async def test_insufficient_sponsors_notifies_once_and_does_not_block_forever(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=720, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=721,
                first_name="Referred",
                referrer_id=720,
                sponsors_verified=True,
                sponsor_wave_one=_wave("https://t.me/a"),
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
        # Reward flag stays False — an insufficient batch must not
        # permanently block future (larger) batches from ever paying out.
        self.assertFalse(saved_referred.referral_reward_given)
        self.assertTrue(saved_referred.referral_insufficient_notified)
        bot.send_message.assert_awaited_once()

        # A later cycle brings the referred user's total sponsors over the
        # threshold (simulating the 10-minute sponsor-wall recheck adding a
        # second wave) — this must now pay for BOTH accumulated sponsors.
        async with self.sessions() as session:
            saved_referred = await session.get(User, 721)
            saved_referred.sponsor_wave_two = _wave("https://t.me/b")
            await session.commit()
            await check_referral_reward(saved_referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 720)

        self.assertEqual(float(saved_referrer.stars_balance), 1.0)  # 2 TG * 0.5
        self.assertEqual(saved_referrer.referrals_count, 1)

    async def test_repeat_cycle_pays_only_for_new_sponsors_without_re_incrementing_milestones(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=730, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=731,
                first_name="Referred",
                referrer_id=730,
                sponsors_verified=True,
                sponsor_wave_one=_wave(*[f"https://t.me/tg{i}" for i in range(6)]),
            )
            session.add_all((referrer, referred))
            await session.commit()

            await check_referral_reward(referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 730)
        self.assertEqual(float(saved_referrer.stars_balance), 3.0)  # 6 * 0.5
        self.assertEqual(saved_referrer.referrals_count, 1)

        # Simulate the recheck scheduler unlocking 2 more brand-new sponsors.
        async with self.sessions() as session:
            saved_referred = await session.get(User, 731)
            saved_referred.sponsor_wave_two = _wave("https://t.me/new1", "https://t.me/new2")
            await session.commit()
            await check_referral_reward(saved_referred, session)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 730)
            saved_referred = await session.get(User, 731)

        # +2 new TG sponsors * 0.5 = +1.0, on top of the first 3.0.
        self.assertEqual(float(saved_referrer.stars_balance), 4.0)
        # referrals_count must NOT increment again for the same referred user.
        self.assertEqual(saved_referrer.referrals_count, 1)
        rewarded = set(json.loads(saved_referred.rewarded_sponsor_urls))
        self.assertEqual(len(rewarded), 8)

    async def test_bot_side_verification_excludes_unconfirmed_tg_sponsor(self) -> None:
        async with self.sessions() as session:
            referrer = User(user_id=740, first_name="Referrer", stars_balance=Decimal(0))
            referred = User(
                user_id=741,
                first_name="Referred",
                referrer_id=740,
                sponsors_verified=True,
                sponsor_wave_one=_wave("https://t.me/good1", "https://t.me/good2", "https://t.me/fake"),
            )
            session.add_all((referrer, referred))
            await session.commit()

            bot = _bot(confirmed_statuses={"@fake": "left"})
            await check_referral_reward(referred, session, bot)

        async with self.sessions() as session:
            saved_referrer = await session.get(User, 740)
            saved_referred = await session.get(User, 741)

        # Only the 2 bot-confirmed sponsors get paid; the unconfirmed one
        # is excluded this cycle but not permanently lost.
        self.assertEqual(float(saved_referrer.stars_balance), 1.0)  # 2 * 0.5
        rewarded = set(json.loads(saved_referred.rewarded_sponsor_urls))
        self.assertEqual(rewarded, {"https://t.me/good1", "https://t.me/good2"})


if __name__ == "__main__":
    unittest.main()
