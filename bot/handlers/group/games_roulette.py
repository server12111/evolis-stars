import re
from decimal import Decimal

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.settings import SettingsRepository
from bot.services.chat_eligibility import credit_stars
from bot.services.chat_games import (
    get_roulette_coeff,
    normalize_roulette_color,
    place_bet,
    record_result,
    roulette_spin,
)

router = Router()

_PATTERN = re.compile(r"^(\S+)\s+(\d+(?:[.,]\d+)?)\s*$")
_COLOR_WORDS = {"red", "ред", "black", "блек", "блэк", "чёрный", "черный", "красный"}
_COLOR_RU = {"red": "красный", "black": "чёрный", "white": "белый", "green": "зелёный"}
_COLOR_EMOJI = {"red": "🔴", "black": "⚫️", "white": "⚪️", "green": "🟢"}


def _matches_roulette_command(message: Message) -> bool:
    if not message.text:
        return False
    match = _PATTERN.match(message.text.strip())
    return bool(match and match.group(1).lower() in _COLOR_WORDS)


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

    ok, error = await place_bet(session, message.from_user.id, bet, min_bet)
    if not ok:
        await message.reply(error)
        return

    result_color = roulette_spin()
    coeff = await get_roulette_coeff(session)
    won = result_color == color
    payout = round(bet * coeff, 2) if won else 0.0

    if won:
        await credit_stars(session, message.from_user.id, Decimal(str(payout)))

    await record_result(
        session, message.from_user.id, message.chat.id, "roulette",
        bet, payout, "roulette_total_bet", "roulette_total_payout",
    )

    emoji = _COLOR_EMOJI[result_color]
    outcome_name = _COLOR_RU[result_color]
    if won:
        text = (
            f"{emoji} Выпало: <b>{outcome_name}</b>!\n\n"
            f"🎉 Угадал! Выигрыш: <b>+{payout:.2f} ⭐</b>"
        )
    else:
        text = f"{emoji} Выпало: <b>{outcome_name}</b>.\n\n❌ Ставка сгорела: -{bet:.2f} ⭐"
    await message.reply(text, parse_mode="HTML")
