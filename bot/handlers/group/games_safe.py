import re
from decimal import Decimal

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.chat_game import ChatGameRoundRepository
from bot.database.repositories.settings import SettingsRepository
from bot.services.chat_eligibility import credit_stars
from bot.services.chat_games import (
    SAFE_MAX_ATTEMPTS,
    count_position_matches,
    generate_safe_code,
    get_safe_coeffs,
    place_bet,
    record_result,
    safe_payout_multiplier,
)

router = Router()

_START_PATTERN = re.compile(r"^сейф\s+(\d+(?:[.,]\d+)?)\s*$", re.IGNORECASE)
_GUESS_PATTERN = re.compile(r"^\d{5}$")


def _matches_safe_start(message: Message) -> bool:
    return bool(message.text and _START_PATTERN.match(message.text.strip()))


def _matches_safe_guess(message: Message) -> bool:
    return bool(message.text and _GUESS_PATTERN.match(message.text.strip()))


@router.message(_matches_safe_start)
async def msg_safe_start(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    match = _START_PATTERN.match(message.text.strip())
    bet = float(match.group(1).replace(",", "."))

    settings_repo = SettingsRepository(session)
    if not await settings_repo.get_bool("safe_enabled", True):
        await message.reply("🔐 Сейф временно недоступен.")
        return

    round_repo = ChatGameRoundRepository(session)
    existing = await round_repo.get_active(message.chat.id, message.from_user.id, "safe")
    if existing:
        await message.reply("⚠️ У тебя уже есть открытый сейф в этом чате — продолжай его (пришли 5 цифр).")
        return

    min_bet = await settings_repo.get_float("safe_min_bet", 1.0)
    ok, error = await place_bet(session, message.from_user.id, bet, min_bet)
    if not ok:
        await message.reply(error)
        return

    secret = generate_safe_code()
    created = await round_repo.create(
        message.chat.id, message.from_user.id, "safe", bet, {"secret": secret, "attempts": []}
    )
    if created is None:
        # Lost the create race against a concurrent start — refund.
        await credit_stars(session, message.from_user.id, Decimal(str(bet)))
        await message.reply("⚠️ У тебя уже есть открытый сейф в этом чате.")
        return

    await message.reply(
        f"🔐 <b>Сейф открыт!</b>\n\nСтавка: <b>{bet:.2f} ⭐</b>\n"
        f"Я загадал код из 5 цифр. У тебя {SAFE_MAX_ATTEMPTS} попыток угадать — "
        f"просто пришли 5 цифр (например: 12345).",
        parse_mode="HTML",
    )


async def _settle_safe(
    message: Message, session: AsyncSession, round_repo: ChatGameRoundRepository, round_, best_match: int
) -> None:
    bet = float(round_.bet)
    coeff_3, coeff_4, coeff_5 = await get_safe_coeffs(session)
    multiplier = safe_payout_multiplier(best_match, coeff_3, coeff_4, coeff_5)
    payout = round(bet * multiplier, 2)

    await round_repo.delete(round_)
    if payout > 0:
        await credit_stars(session, message.from_user.id, Decimal(str(payout)))
    await record_result(
        session, message.from_user.id, message.chat.id, "safe",
        bet, payout, "safe_total_bet", "safe_total_payout",
    )

    if payout > 0:
        text = (
            f"🏆 Лучший результат: <b>{best_match}/5</b> цифр на своих местах.\n\n"
            f"💰 Выплата: <b>+{payout:.2f} ⭐</b>"
        )
    else:
        text = f"❌ Лучший результат: <b>{best_match}/5</b>. Ставка сгорела: -{bet:.2f} ⭐"
    await message.reply(text, parse_mode="HTML")


@router.message(_matches_safe_guess)
async def msg_safe_guess(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    round_repo = ChatGameRoundRepository(session)
    round_ = await round_repo.get_active(message.chat.id, message.from_user.id, "safe")
    if round_ is None:
        return  # not a safe guess — just a random 5-digit message, ignore silently

    guess = message.text.strip()
    state = round_repo.load_state(round_)
    secret = state["secret"]
    attempts: list[int] = state["attempts"]

    match_count = count_position_matches(secret, guess)
    attempts.append(match_count)

    if match_count == 5:
        await _settle_safe(message, session, round_repo, round_, best_match=5)
        return

    if len(attempts) >= SAFE_MAX_ATTEMPTS:
        await _settle_safe(message, session, round_repo, round_, best_match=max(attempts))
        return

    state["attempts"] = attempts
    await round_repo.save_state(round_, state)
    remaining = SAFE_MAX_ATTEMPTS - len(attempts)
    await message.reply(
        f"🔢 «{guess}» — совпало на своих местах: <b>{match_count}/5</b>.\n"
        f"Осталось попыток: <b>{remaining}</b>.",
        parse_mode="HTML",
    )
