import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.database.repositories.settings import DEFAULT_SETTINGS
from bot.handlers.games import _execute_game

# Telegram dice/darts/bowling: uniform 1-6. Basketball/football: uniform 1-5.
# Slot machine: uniform 1-64. These ranges are fixed by the Bot API — the
# bot can't bias them, so a flat ~20% edge has to come entirely from the
# payout multiplier on each winning side.
TARGET_RTP = 0.80
TOLERANCE = 0.005  # rounding to 2-4 decimal places in settings can drift slightly


def _coeff(key: str) -> float:
    return float(DEFAULT_SETTINGS[key])


class FootballRtpTests(unittest.TestCase):
    def test_goal_side_hits_target_rtp(self) -> None:
        rtp = (2 / 5) * _coeff("game_football_coeff_goal")
        self.assertAlmostEqual(rtp, TARGET_RTP, delta=TOLERANCE)

    def test_miss_side_hits_target_rtp(self) -> None:
        rtp = (3 / 5) * _coeff("game_football_coeff_miss")
        self.assertAlmostEqual(rtp, TARGET_RTP, delta=TOLERANCE)

    def test_neither_side_gives_the_player_positive_ev(self) -> None:
        for key, p in (("game_football_coeff_goal", 2 / 5), ("game_football_coeff_miss", 3 / 5)):
            with self.subTest(key=key):
                self.assertLess(p * _coeff(key), 1.0)


class BasketballRtpTests(unittest.TestCase):
    def test_all_sides_hit_target_rtp(self) -> None:
        cases = [
            ("game_basketball_coeff_clean", 1 / 5),
            ("game_basketball_coeff_any", 2 / 5),
            ("game_basketball_coeff_stuck", 1 / 5),
            ("game_basketball_coeff_miss", 2 / 5),
        ]
        for key, p in cases:
            with self.subTest(key=key):
                self.assertAlmostEqual(p * _coeff(key), TARGET_RTP, delta=TOLERANCE)


class BowlingRtpTests(unittest.TestCase):
    def test_all_sides_hit_target_rtp(self) -> None:
        cases = [
            ("game_bowling_coeff_strike", 1 / 6),
            ("game_bowling_coeff_partial", 4 / 6),
            ("game_bowling_coeff_miss", 1 / 6),
        ]
        for key, p in cases:
            with self.subTest(key=key):
                self.assertAlmostEqual(p * _coeff(key), TARGET_RTP, delta=TOLERANCE)

    def test_partial_no_longer_gives_the_player_positive_ev(self) -> None:
        # Previously partial paid 2.0x on a 4/6 chance (133% RTP) — a
        # guaranteed long-run winner for the player. Must be fixed.
        rtp = (4 / 6) * _coeff("game_bowling_coeff_partial")
        self.assertLess(rtp, 1.0)


class DiceRtpTests(unittest.TestCase):
    def test_hits_target_rtp(self) -> None:
        rtp = 0.5 * _coeff("game_dice_coeff")
        self.assertAlmostEqual(rtp, TARGET_RTP, delta=TOLERANCE)


class DartsRtpTests(unittest.TestCase):
    def test_both_sides_hit_target_rtp(self) -> None:
        for key in ("game_darts_coeff_bullseye", "game_darts_coeff_bounce"):
            with self.subTest(key=key):
                rtp = (1 / 6) * _coeff(key)
                self.assertAlmostEqual(rtp, TARGET_RTP, delta=TOLERANCE)


class SlotsRtpTests(unittest.TestCase):
    def test_overall_rtp_hits_target(self) -> None:
        jackpot = _coeff("game_slots_coeff1")
        fruits = _coeff("game_slots_coeff2")
        rtp = (1 * jackpot + 3 * fruits) / 64
        self.assertAlmostEqual(rtp, TARGET_RTP, delta=TOLERANCE)

    def test_no_longer_a_75_percent_edge_outlier(self) -> None:
        jackpot = _coeff("game_slots_coeff1")
        fruits = _coeff("game_slots_coeff2")
        rtp = (1 * jackpot + 3 * fruits) / 64
        self.assertGreater(rtp, 0.5)


def _fake_bot(dice_value: int) -> SimpleNamespace:
    dice_msg = SimpleNamespace(dice=SimpleNamespace(value=dice_value))
    return SimpleNamespace(send_dice=AsyncMock(return_value=dice_msg))


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class ExecuteGameEndToEndTests(ChatModelsTestCase):
    async def test_bowling_partial_win_pays_the_new_lower_coeff(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
            db_user = await session.get(User, 1)
            won, payout, value = await _execute_game(
                _fake_bot(3), 1, session, db_user, "bowling", 10.0, "partial",
            )
        self.assertTrue(won)
        self.assertEqual(payout, 12.0)  # 10 * 1.2, not the old 10 * 2.0 = 20
        self.assertEqual(float(db_user.stars_balance), 12.0)

    async def test_football_miss_win_pays_the_new_lower_coeff(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=2, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
            db_user = await session.get(User, 2)
            won, payout, value = await _execute_game(
                _fake_bot(1), 1, session, db_user, "football", 10.0, "miss",
            )
        self.assertTrue(won)
        self.assertAlmostEqual(payout, 13.3, places=2)  # 10 * 1.33, not the old 10 * 2.2 = 22

    async def test_slots_jackpot_pays_the_new_higher_coeff(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=3, first_name="U", stars_balance=Decimal("0")))
            await session.commit()
            db_user = await session.get(User, 3)
            won, payout, value = await _execute_game(
                _fake_bot(64), 1, session, db_user, "slots", 1.0,
            )
        self.assertTrue(won)
        self.assertEqual(payout, 42.2)  # not the old 1 * 10.0 = 10


if __name__ == "__main__":
    unittest.main()
