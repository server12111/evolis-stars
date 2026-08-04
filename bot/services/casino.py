import random

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.settings import SettingsRepository
from bot.services.house_edge import is_in_recovery

WHEEL_OUTCOMES = [0.1, 50.0]
# 0.1*0.986 + 50*0.014 = 79.86% RTP -> ~20% house edge (was 15.15%).
_WHEEL_NORMAL = [98.60, 1.40]
_WHEEL_PUNISH = [99.5, 0.5]

CASE_PRIZES = {
    1: [0.1, 0.3, 0.5, 0.7, 0.9, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.5, 3.0, 3.5],
    3: [0.5, 0.7, 0.9, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
    5: [1.0, 3.0, 5.0, 6.0, 6.5, 7.0, 7.5, 8.0, 9.0],
}

# Retargeted to ~80% RTP per tier (was 54.6% / 38.7% / 75.6% respectively —
# tier 3 in particular had a ~61% house edge).
_CASE_NORMAL = {
    1: [17, 15, 13, 11, 10, 8, 7, 6, 5, 3, 2, 1.5, 1, 0.5],
    3: [6, 7, 8, 9, 9, 10, 11, 11, 11, 8, 10],
    5: [27, 24, 19, 9, 6, 5, 4, 3, 3],
}

_CASE_PUNISH = {
    1: [50, 25, 12, 6, 3, 2, 1, 0.5, 0.3, 0.1, 0.05, 0.03, 0.01, 0.01],
    3: [45, 25, 15, 8, 4, 2, 0.5, 0.3, 0.1, 0.05, 0.05],
    5: [75, 15, 6, 1.5, 1, 0.5, 0.3, 0.4, 0.3],
}


def _weighted_choice(items: list, weights: list):
    return random.choices(items, weights=weights, k=1)[0]


async def get_wheel_outcome(session: AsyncSession) -> float:
    weights = _WHEEL_PUNISH if await is_in_recovery(session, "wheel") else _WHEEL_NORMAL
    return _weighted_choice(WHEEL_OUTCOMES, weights)


async def get_case_outcome(session: AsyncSession, tier: int) -> float:
    weights = _CASE_PUNISH[tier] if await is_in_recovery(session, f"case_{tier}") else _CASE_NORMAL[tier]
    return _weighted_choice(CASE_PRIZES[tier], weights)


async def update_casino_profit(session: AsyncSession, game_type: str, bet: float, payout: float) -> None:
    repo = SettingsRepository(session)
    await repo.add_float(f"{game_type}_total_bet", bet)
    await repo.add_float(f"{game_type}_total_payout", payout)
