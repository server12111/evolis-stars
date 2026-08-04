import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.repositories.chat_game import ChatGameRoundRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.group.games_doors import doors_pick_kb, doors_result_kb
from bot.keyboards.group.registration import registration_required_kb
from bot.services.chat_eligibility import credit_stars
from bot.services.chat_games import (
    DOORS_LEVELS,
    doors_generate_safe_positions,
    get_doors_coeff,
    place_bet,
    record_result,
)
from bot.services.house_edge import is_in_recovery

settings = get_settings()

router = Router()

_START_PATTERN = re.compile(r"^двери\s+(\d+(?:[.,]\d+)?)\s*$", re.IGNORECASE)


def _matches_doors_start(message: Message) -> bool:
    return bool(message.text and _START_PATTERN.match(message.text.strip()))


async def _show_level(sendable, round_, edit: bool) -> None:
    level_display = round_.level + 1
    text = (
        f"🚪 <b>Двери</b>\n\nСтавка: <b>{float(round_.bet):.2f} ⭐</b>\n"
        f"Уровень: <b>{level_display}/{DOORS_LEVELS}</b>\n\n"
        "За двумя дверями приз, за двумя — мина. Выбери дверь:"
    )
    kb = doors_pick_kb()
    if edit:
        try:
            await sendable.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await sendable.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await sendable.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(_matches_doors_start)
async def msg_doors_start(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    match = _START_PATTERN.match(message.text.strip())
    bet = float(match.group(1).replace(",", "."))

    settings_repo = SettingsRepository(session)
    if not await settings_repo.get_bool("doors_enabled", True):
        await message.reply("🚪 Двери временно недоступны.")
        return

    round_repo = ChatGameRoundRepository(session)
    existing = await round_repo.get_active(message.chat.id, message.from_user.id, "doors")
    if existing:
        await message.reply("⚠️ У тебя уже есть активная игра «Двери» в этом чате.")
        return

    min_bet = await settings_repo.get_float("doors_min_bet", 1.0)
    max_bet = await settings_repo.get_float("doors_max_bet", 500.0)
    ok, error, needs_registration = await place_bet(session, message.from_user.id, bet, min_bet, max_bet)
    if not ok:
        kb = registration_required_kb(settings.bot_username) if needs_registration else None
        await message.reply(error, reply_markup=kb)
        return

    in_recovery = await is_in_recovery(session, "doors")
    safe_positions = doors_generate_safe_positions(punish=in_recovery)
    created = await round_repo.create(
        message.chat.id, message.from_user.id, "doors",
        bet, {"safe_positions": safe_positions, "in_recovery": in_recovery},
    )
    if created is None:
        await credit_stars(session, message.from_user.id, Decimal(str(bet)))
        await message.reply("⚠️ У тебя уже есть активная игра «Двери» в этом чате.")
        return

    await _show_level(message, created, edit=False)


@router.callback_query(F.data.startswith("doors:pick:"))
async def cb_doors_pick(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    try:
        picked = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    round_repo = ChatGameRoundRepository(session)
    round_ = await round_repo.get_active(callback.message.chat.id, callback.from_user.id, "doors")
    if round_ is None:
        await callback.answer("⚠️ Раунд не найден или уже завершён.", show_alert=True)
        return

    state = round_repo.load_state(round_)
    safe_positions = state["safe_positions"]

    if picked not in safe_positions:
        bet = float(round_.bet)
        await round_repo.delete(round_)
        await record_result(
            session, callback.from_user.id, callback.message.chat.id, "doors",
            bet, 0.0, "doors_total_bet", "doors_total_payout",
        )
        text = f"💣 <b>Мина!</b> Ставка сгорела: -{bet:.2f} ⭐"
        try:
            await callback.message.edit_text(text, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, parse_mode="HTML")
        await callback.answer()
        return

    new_level = round_.level + 1
    await round_repo.save_state(round_, state, level=new_level)
    coeff = await get_doors_coeff(session, new_level)
    payout = round(float(round_.bet) * coeff, 2)
    is_final = new_level >= DOORS_LEVELS

    text = (
        f"🎁 <b>Приз!</b> Уровень <b>{new_level}/{DOORS_LEVELS}</b> пройден.\n\n"
        f"Множитель: <b>×{coeff:.2f}</b> | Возможный выигрыш: <b>{payout:.2f} ⭐</b>"
    )
    kb = doors_result_kb(is_final_level=is_final)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "doors:next")
async def cb_doors_next(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    round_repo = ChatGameRoundRepository(session)
    round_ = await round_repo.get_active(callback.message.chat.id, callback.from_user.id, "doors")
    if round_ is None:
        await callback.answer("⚠️ Раунд не найден или уже завершён.", show_alert=True)
        return
    if round_.level >= DOORS_LEVELS:
        await callback.answer()
        return

    state = round_repo.load_state(round_)
    state["safe_positions"] = doors_generate_safe_positions(punish=state.get("in_recovery", False))
    await round_repo.save_state(round_, state)

    await _show_level(callback.message, round_, edit=True)
    await callback.answer()


@router.callback_query(F.data == "doors:cashout")
async def cb_doors_cashout(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    round_repo = ChatGameRoundRepository(session)
    round_ = await round_repo.get_active(callback.message.chat.id, callback.from_user.id, "doors")
    if round_ is None or round_.level == 0:
        await callback.answer("⚠️ Сначала пройди хотя бы один уровень.", show_alert=True)
        return

    coeff = await get_doors_coeff(session, round_.level)
    bet = float(round_.bet)
    payout = round(bet * coeff, 2)

    await round_repo.delete(round_)
    await credit_stars(session, callback.from_user.id, Decimal(str(payout)))
    await record_result(
        session, callback.from_user.id, callback.message.chat.id, "doors",
        bet, payout, "doors_total_bet", "doors_total_payout",
    )

    text = (
        f"💰 <b>Выигрыш забран!</b>\n\nПройдено уровней: <b>{round_.level}/{DOORS_LEVELS}</b>\n"
        f"Множитель: <b>×{coeff:.2f}</b>\nВыплата: <b>+{payout:.2f} ⭐</b>"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()
