import asyncio
import random
import re
from decimal import Decimal

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.settings import SettingsRepository
from bot.services.chat_eligibility import credit_stars
from bot.services.chat_games import (
    COLOR_EMOJI,
    get_roulette_coeff,
    normalize_roulette_color,
    place_bet,
    record_result,
    roulette_spin,
)
from bot.services.house_edge import is_in_recovery

router = Router()

_PATTERN = re.compile(r"^(\S+)\s+(\d+(?:[.,]\d+)?)\s*$")
_COLOR_WORDS = {
    "red", "ред", "black", "блек", "блэк", "чёрный", "черный", "красный",
    "green", "грин", "зелёный", "зеленый",
}
_COLOR_RU = {"red": "красный", "black": "чёрный", "white": "белый", "green": "зелёный"}

# Spin animation: 5 circles in a row, the center one is always white and
# never changes — the 4 side positions flicker red/black/green (green rare,
# matching the real pool odds) while spinning, then lock onto the real
# outcome's color once it lands.
_SIDE_EMOJI_WEIGHTED = (("🔴", 45), ("⚫️", 45), ("🟢", 10))
_SPIN_FRAMES = 4
_SPIN_DELAY = 0.35


def _matches_roulette_command(message: Message) -> bool:
    if not message.text:
        return False
    match = _PATTERN.match(message.text.strip())
    return bool(match and match.group(1).lower() in _COLOR_WORDS)


def _spin_row() -> str:
    colors, weights = zip(*_SIDE_EMOJI_WEIGHTED)
    sides = random.choices(colors, weights=weights, k=4)
    return f"{sides[0]}{sides[1]}⚪️{sides[2]}{sides[3]}"


def _result_row(result_color: str) -> str:
    dot = COLOR_EMOJI[result_color]
    return f"{dot}{dot}⚪️{dot}{dot}"


async def _animate_and_reveal(message: Message, result_color: str, final_text: str) -> None:
    sent = await message.reply(f"🎰 <b>Рулетка крутится...</b>\n\n{_spin_row()}", parse_mode="HTML")
    for _ in range(_SPIN_FRAMES):
        await asyncio.sleep(_SPIN_DELAY)
        try:
            await sent.edit_text(f"🎰 <b>Рулетка крутится...</b>\n\n{_spin_row()}", parse_mode="HTML")
        except Exception:
            pass
    await asyncio.sleep(_SPIN_DELAY)
    try:
        await sent.edit_text(f"{_result_row(result_color)}\n\n{final_text}", parse_mode="HTML")
    except Exception:
        pass


@router.message(_matches_roulette_command)
async def msg_roulette_bet(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    match = _PATTERN.match(message.text.strip())
    color = normalize_roulette_color(match.group(1))
    bet = float(match.group(2).replace(",", "."))

    settings_repo = SettingsRepository(session)
    if not await settings_repo.get_bool("roulette_enabled", True):
        await message.reply("🎰 Рулетка временно недоступна.")
        return
    min_bet = await settings_repo.get_float("roulette_min_bet", 1.0)
    max_bet = await settings_repo.get_float("roulette_max_bet", 500.0)

    ok, error = await place_bet(session, message.from_user.id, bet, min_bet, max_bet)
    if not ok:
        await message.reply(error)
        return

    punish = await is_in_recovery(session, "roulette")
    result_color = roulette_spin(punish=punish)
    coeff = await get_roulette_coeff(session, color)
    won = result_color == color
    payout = round(bet * coeff, 2) if won else 0.0

    if won:
        await credit_stars(session, message.from_user.id, Decimal(str(payout)))

    await record_result(
        session, message.from_user.id, message.chat.id, "roulette",
        bet, payout, "roulette_total_bet", "roulette_total_payout",
        bet_choice=color,
    )

    emoji = COLOR_EMOJI[result_color]
    outcome_name = _COLOR_RU[result_color]
    if won:
        final_text = (
            f"{emoji} Выпало: <b>{outcome_name}</b>!\n\n"
            f"🎉 Угадал! Выигрыш: <b>+{payout:.2f} ⭐</b> (×{coeff:.2f})"
        )
    else:
        final_text = f"{emoji} Выпало: <b>{outcome_name}</b>.\n\n❌ Ставка сгорела: -{bet:.2f} ⭐"

    await _animate_and_reveal(message, result_color, final_text)
