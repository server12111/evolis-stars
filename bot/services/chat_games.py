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


# ─── Safe ─────────────────────────────────────────────────────────────────

SAFE_CODE_LENGTH = 5
SAFE_MAX_ATTEMPTS = 6


def generate_safe_code() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(SAFE_CODE_LENGTH))


def count_position_matches(secret: str, guess: str) -> int:
    return sum(1 for s, g in zip(secret, guess) if s == g)


async def get_safe_coeffs(session: AsyncSession) -> tuple[float, float, float]:
    """Returns (coeff for 3, 4, 5 position-correct digits)."""
    repo = SettingsRepository(session)
    return (
        await repo.get_float("safe_coeff_3", 1.0),
        await repo.get_float("safe_coeff_4", 1.2),
        await repo.get_float("safe_coeff_5", 1.6),
    )


def safe_payout_multiplier(best_match: int, coeff_3: float, coeff_4: float, coeff_5: float) -> float:
    if best_match >= 5:
        return coeff_5
    if best_match == 4:
        return coeff_4
    if best_match == 3:
        return coeff_3
    return 0.0


# ─── Maze ───────────────────────────────────────────────────────────────────
# Modeled on mines_coeff()'s EV-constant trick: base(k) keeps
# P(survive k) * base(k) constant regardless of k, so there's no
# exploitable "best step to stop at" from the base coefficient alone.
# treasure/jackpot layer a small flat bonus on top.

MAZE_TILE_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("trap", 18),
    ("shield", 10),
    ("empty", 37),
    ("treasure", 30),
    ("jackpot", 5),
)
MAZE_SURVIVAL_PROB = 0.82  # 1 - P(trap), used for the base-coefficient formula
MAZE_TREASURE_BONUS = 0.03
MAZE_JACKPOT_BONUS = 0.15
MAZE_MAX_SHIELDS = 2


def maze_draw_tile() -> str:
    tiles, weights = zip(*MAZE_TILE_WEIGHTS)
    return random.choices(tiles, weights=weights, k=1)[0]


def maze_base_coeff(step: int, house_edge: float, max_coeff: float) -> float:
    if step <= 0:
        return 1.0
    raw = (1 / (MAZE_SURVIVAL_PROB ** step)) * (1 - house_edge)
    return round(min(raw, max_coeff), 4)


async def get_maze_params(session: AsyncSession) -> tuple[float, float]:
    """Returns (house_edge, max_coeff)."""
    repo = SettingsRepository(session)
    return (
        await repo.get_float("maze_house_edge", 0.24),
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
