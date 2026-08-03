import random
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import GameSession, User
from bot.database.repositories.settings import SettingsRepository
from bot.services.chat_eligibility import debit_stars_if_enough


async def place_bet(session: AsyncSession, user_id: int, bet: float, min_bet: float) -> tuple[bool, str]:
    """Validate + atomically deduct a chat-game bet. Returns (ok, error_text)."""
    if not (bet > 0) or bet < min_bet:
        return False, f"❌ Мин. ставка: {min_bet:.0f} ⭐."
    user = await session.get(User, user_id)
    if user is None:
        return False, "❌ Нужно быть зарегистрированным в боте — напиши /start в личных сообщениях."
    ok = await debit_stars_if_enough(session, user_id, Decimal(str(bet)))
    if not ok:
        return False, "❌ Недостаточно звёзд на балансе."
    return True, ""


async def record_result(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    game_type: str,
    bet: float,
    payout: float,
    total_bet_key: str,
    total_payout_key: str,
) -> None:
    result = "win" if payout > 0 else "lose"
    session.add(
        GameSession(
            user_id=user_id,
            chat_id=chat_id,
            game_type=game_type,
            bet=Decimal(str(bet)),
            payout=Decimal(str(payout)),
            result=result,
        )
    )
    settings_repo = SettingsRepository(session)
    await settings_repo.add_float(total_bet_key, bet)
    await settings_repo.add_float(total_payout_key, payout)
    await session.commit()

# ─── Roulette ───────────────────────────────────────────────────────────────
# 11-cube pool: 2 white, 4 black, 4 red, 1 green. Only red/black are
# bettable colors — white/green just make a red/black bet lose.

ROULETTE_CUBES: tuple[tuple[str, int], ...] = (
    ("white", 2),
    ("black", 4),
    ("red", 4),
    ("green", 1),
)

_ROULETTE_COLOR_ALIASES = {
    "red": "red", "ред": "red", "красный": "red", "красное": "red", "краснный": "red",
    "black": "black", "блек": "black", "блэк": "black", "чёрный": "black", "черный": "black", "черное": "black",
}


def roulette_spin() -> str:
    colors, weights = zip(*ROULETTE_CUBES)
    return random.choices(colors, weights=weights, k=1)[0]


def normalize_roulette_color(raw: str) -> str | None:
    return _ROULETTE_COLOR_ALIASES.get(raw.strip().lower())


async def get_roulette_coeff(session: AsyncSession) -> float:
    return await SettingsRepository(session).get_float("roulette_coeff_red_black", 1.6)


# ─── Maze ───────────────────────────────────────────────────────────────────
# base(k) compounds a per-step edge: base(k) = ((1-house_edge)/MAZE_SURVIVAL_PROB)**k.
# Each surviving step multiplies EV by exactly (1-house_edge), so the payout
# is guaranteed to strictly increase with every step (unlike a flat one-time
# discount applied to a growth curve, which can make an early step's payout
# dip below the starting 1.0x — that was the original bug). house_edge is
# clamped below (1 - MAZE_SURVIVAL_PROB) so the per-step growth factor can
# never drop to <=1 no matter what an admin sets it to.
# treasure/jackpot layer a small flat bonus on top.

MAZE_TILE_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("trap", 18),
    ("shield", 10),
    ("empty", 37),
    ("treasure", 30),
    ("jackpot", 5),
)
MAZE_SURVIVAL_PROB = 0.82  # 1 - P(trap), used for the base-coefficient formula
MAZE_MAX_HOUSE_EDGE = 0.17  # keeps growth factor (1-edge)/MAZE_SURVIVAL_PROB > 1
MAZE_TREASURE_BONUS = 0.03
MAZE_JACKPOT_BONUS = 0.15
MAZE_MAX_SHIELDS = 2


def maze_draw_tile() -> str:
    tiles, weights = zip(*MAZE_TILE_WEIGHTS)
    return random.choices(tiles, weights=weights, k=1)[0]


def maze_base_coeff(step: int, house_edge: float, max_coeff: float) -> float:
    if step <= 0:
        return 1.0
    edge = min(house_edge, MAZE_MAX_HOUSE_EDGE)
    growth = (1 - edge) / MAZE_SURVIVAL_PROB
    raw = growth ** step
    return round(min(raw, max_coeff), 4)


async def get_maze_params(session: AsyncSession) -> tuple[float, float]:
    """Returns (house_edge, max_coeff)."""
    repo = SettingsRepository(session)
    return (
        await repo.get_float("maze_house_edge", 0.1),
        await repo.get_float("maze_max_coeff", 10.0),
    )


# ─── Doors ──────────────────────────────────────────────────────────────────

DOORS_PER_LEVEL = 4
DOORS_SAFE_PER_LEVEL = 2
DOORS_LEVELS = 10
_DOORS_DEFAULT_COEFFS = [1.6, 3.2, 6.4, 12.8, 25.6, 51.2, 102.4, 204.8, 409.6, 819.2]


def doors_generate_safe_positions() -> list[int]:
    return random.sample(range(DOORS_PER_LEVEL), DOORS_SAFE_PER_LEVEL)


async def get_doors_coeff(session: AsyncSession, level: int) -> float:
    """level is 1-indexed (level 1 = first door pick)."""
    idx = max(1, min(level, DOORS_LEVELS)) - 1
    repo = SettingsRepository(session)
    return await repo.get_float(f"door_coeff_{idx + 1}", _DOORS_DEFAULT_COEFFS[idx])


# ─── Tower (group) ─────────────────────────────────────────────────────────
# Own settings namespace (chat_tower_*) — deliberately separate from the
# private-chat Tower's tower_coeff_* so this group game can be rebalanced
# without touching the private game. coeff(k) = fair(k-1) i.e. one level
# "behind" fair value (fair(k) = 1.5**k, since survival per level is 2/3) —
# level 1 is a pure push (1.00x), real profit only starts from level 2 on.
CHAT_TOWER_LEVELS = 8
CHAT_TOWER_DEFAULT_COEFFS = [1.00, 1.50, 2.25, 3.38, 5.06, 7.59, 11.39, 17.09]


async def get_chat_tower_coeff(session: AsyncSession, level: int) -> float:
    """level is 0-indexed (level 0 = first pick's payout)."""
    idx = max(0, min(level, len(CHAT_TOWER_DEFAULT_COEFFS) - 1))
    repo = SettingsRepository(session)
    return await repo.get_float(f"chat_tower_coeff_{idx}", CHAT_TOWER_DEFAULT_COEFFS[idx])
