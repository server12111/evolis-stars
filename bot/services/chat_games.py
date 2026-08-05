import random
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import GameSession, User
from bot.database.repositories.chat_game import ChatGameRoundRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.group.registration import REGISTRATION_REQUIRED_TEXT
from bot.services.chat_eligibility import debit_stars_if_enough

# Russian labels for every persisted multi-step chat game (roulette resolves
# instantly and never has an active round of its own, so it's not listed
# here — but a bet on it is still blocked below while any of these is active).
GAME_TYPE_LABELS = {"doors": "Двери", "maze": "Лабиринт", "tower": "Башня"}


async def place_bet(
    session: AsyncSession, user_id: int, bet: float, min_bet: float, max_bet: float | None = None,
) -> tuple[bool, str, bool]:
    """Validate + atomically deduct a chat-game bet.
    Returns (ok, error_text, needs_registration) — needs_registration is
    True only when the error is specifically "not registered yet", so the
    caller knows to attach the registration-link button."""
    if not (bet > 0) or bet < min_bet:
        return False, f"❌ Мин. ставка: {min_bet:.0f} RP⭐️.", False
    if max_bet is not None and bet > max_bet:
        return False, f"❌ Макс. ставка: {max_bet:.0f} RP⭐️.", False
    active = await ChatGameRoundRepository(session).get_any_active(user_id)
    if active is not None:
        label = GAME_TYPE_LABELS.get(active.game_type, active.game_type)
        return False, f"⚠️ У тебя уже есть активная игра «{label}» — заверши её, прежде чем начать новую.", False
    user = await session.get(User, user_id)
    if user is None:
        return False, REGISTRATION_REQUIRED_TEXT, True
    ok = await debit_stars_if_enough(session, user_id, Decimal(str(bet)))
    if not ok:
        return False, "❌ Недостаточно звёзд на балансе.", False
    return True, "", False


async def record_result(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    game_type: str,
    bet: float,
    payout: float,
    total_bet_key: str,
    total_payout_key: str,
    bet_choice: str | None = None,
    result_choice: str | None = None,
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
            bet_choice=bet_choice,
            result_choice=result_choice,
        )
    )
    settings_repo = SettingsRepository(session)
    await settings_repo.add_float(total_bet_key, bet)
    await settings_repo.add_float(total_payout_key, payout)
    await session.commit()

# ─── Roulette ───────────────────────────────────────────────────────────────
# 11-cube pool: 2 white, 4 black, 4 red, 1 green (rarest). White is never
# bettable — it's the pool's built-in "everyone bet-on-a-color loses" slot.

ROULETTE_CUBES: tuple[tuple[str, int], ...] = (
    ("white", 2),
    ("black", 4),
    ("red", 4),
    ("green", 1),
)
# Recovery-mode pool: crushes the odds of any bettable color landing,
# concentrating weight on the un-bettable white slot. See services/house_edge.py.
ROULETTE_CUBES_PUNISH: tuple[tuple[str, int], ...] = (
    ("white", 6),
    ("black", 2),
    ("red", 2),
    ("green", 0),
)

COLOR_EMOJI = {"red": "🔴", "black": "⚫️", "white": "⚪️", "green": "🟢"}

_ROULETTE_COLOR_ALIASES = {
    "red": "red", "ред": "red", "красный": "red", "красное": "red", "краснный": "red",
    "black": "black", "блек": "black", "блэк": "black", "чёрный": "black", "черный": "black", "черное": "black",
    "green": "green", "грин": "green", "зелёный": "green", "зеленый": "green", "зеленое": "green",
}


def roulette_spin(punish: bool = False) -> str:
    pool = ROULETTE_CUBES_PUNISH if punish else ROULETTE_CUBES
    colors, weights = zip(*pool)
    return random.choices(colors, weights=weights, k=1)[0]


def normalize_roulette_color(raw: str) -> str | None:
    return _ROULETTE_COLOR_ALIASES.get(raw.strip().lower())


async def get_roulette_coeff(session: AsyncSession, color: str) -> float:
    key = "roulette_coeff_green" if color == "green" else "roulette_coeff_red_black"
    default = 8.8 if color == "green" else 2.2
    return await SettingsRepository(session).get_float(key, default)


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
# Recovery-mode tile weights: trap share more than doubles, treasure/jackpot
# shrink. See services/house_edge.py.
MAZE_TILE_WEIGHTS_PUNISH: tuple[tuple[str, int], ...] = (
    ("trap", 40),
    ("shield", 5),
    ("empty", 40),
    ("treasure", 13),
    ("jackpot", 2),
)
MAZE_SURVIVAL_PROB = 0.82  # 1 - P(trap), used for the base-coefficient formula
MAZE_MAX_HOUSE_EDGE = 0.17  # keeps growth factor (1-edge)/MAZE_SURVIVAL_PROB > 1
MAZE_TREASURE_BONUS = 0.03
MAZE_JACKPOT_BONUS = 0.15
MAZE_MAX_SHIELDS = 2


def maze_draw_tile(punish: bool = False) -> str:
    pool = MAZE_TILE_WEIGHTS_PUNISH if punish else MAZE_TILE_WEIGHTS
    tiles, weights = zip(*pool)
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
        await repo.get_float("maze_house_edge", 0.15),
        await repo.get_float("maze_max_coeff", 10.0),
    )


# ─── Doors ──────────────────────────────────────────────────────────────────

DOORS_PER_LEVEL = 4
DOORS_SAFE_PER_LEVEL = 2
DOORS_SAFE_PER_LEVEL_PUNISH = 1  # recovery mode: 1-of-4 safe instead of 2-of-4
DOORS_LEVELS = 10
_DOORS_DEFAULT_COEFFS = [1.05, 1.15, 1.30, 1.50, 1.75, 2.10, 2.50, 3.00, 3.80, 5.00]


def doors_generate_safe_positions(punish: bool = False) -> list[int]:
    n = DOORS_SAFE_PER_LEVEL_PUNISH if punish else DOORS_SAFE_PER_LEVEL
    return random.sample(range(DOORS_PER_LEVEL), n)


async def get_doors_coeff(session: AsyncSession, level: int) -> float:
    """level is 1-indexed (level 1 = first door pick)."""
    idx = max(1, min(level, DOORS_LEVELS)) - 1
    repo = SettingsRepository(session)
    return await repo.get_float(f"door_coeff_{idx + 1}", _DOORS_DEFAULT_COEFFS[idx])


# ─── Tower (group) ─────────────────────────────────────────────────────────
# Own settings namespace (chat_tower_*) — deliberately separate from the
# private-chat Tower's tower_coeff_* so this group game can be rebalanced
# without touching the private game.
CHAT_TOWER_LEVELS = 8
CHAT_TOWER_DEFAULT_COEFFS = [1.05, 1.20, 1.40, 1.65, 1.95, 2.30, 2.70, 3.20]


async def get_chat_tower_coeff(session: AsyncSession, level: int) -> float:
    """level is 0-indexed (level 0 = first pick's payout)."""
    idx = max(0, min(level, len(CHAT_TOWER_DEFAULT_COEFFS) - 1))
    repo = SettingsRepository(session)
    return await repo.get_float(f"chat_tower_coeff_{idx}", CHAT_TOWER_DEFAULT_COEFFS[idx])
