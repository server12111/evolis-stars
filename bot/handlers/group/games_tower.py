import random
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.chat_game import ChatGameRoundRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.group.games_tower import tower_playing_kb
from bot.services.chat_eligibility import credit_stars
from bot.services.chat_games import get_chat_tower_coeff, place_bet, record_result
from bot.services.house_edge import is_in_recovery

router = Router()

_START_PATTERN = re.compile(r"^башня\s+(\d+(?:[.,]\d+)?)\s*$", re.IGNORECASE)


def _matches_tower_start(message: Message) -> bool:
    return bool(message.text and _START_PATTERN.match(message.text.strip()))


@router.message(_matches_tower_start)
async def msg_tower_start(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    match = _START_PATTERN.match(message.text.strip())
    bet = float(match.group(1).replace(",", "."))

    settings_repo = SettingsRepository(session)
    if not await settings_repo.get_bool("chat_tower_enabled", True):
        await message.reply("🗼 Башня временно недоступна.")
        return

    round_repo = ChatGameRoundRepository(session)
    existing = await round_repo.get_active(message.chat.id, message.from_user.id, "tower")
    if existing:
        await message.reply("⚠️ У тебя уже есть активная игра «Башня» в этом чате.")
        return

    min_bet = await settings_repo.get_float("chat_tower_min_bet", 1.0)
    max_bet = await settings_repo.get_float("chat_tower_max_bet", 500.0)
    ok, error = await place_bet(session, message.from_user.id, bet, min_bet, max_bet)
    if not ok:
        await message.reply(error)
        return

    max_levels = await settings_repo.get_int("chat_tower_levels", 8)
    mine_count = 2 if await is_in_recovery(session, "chat_tower") else 1
    mines = [random.sample(range(3), mine_count) for _ in range(max_levels)]
    created = await round_repo.create(
        message.chat.id, message.from_user.id, "tower", bet, {"mines": mines, "history": []}
    )
    if created is None:
        await credit_stars(session, message.from_user.id, Decimal(str(bet)))
        await message.reply("⚠️ У тебя уже есть активная игра «Башня» в этом чате.")
        return

    text = (
        f"🗼 <b>Башня</b>\n\nСтавка: <b>{bet:.2f} ⭐</b> | Уровень: <b>1/{max_levels}</b>\n\n"
        "3 плитки, 1 мина. Выбери плитку без мины:"
    )
    kb = tower_playing_kb(max_levels, mines, [], active_level=0, coeff=0.0, payout=0.0)
    await message.reply(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("chattower:pick:"))
async def cb_tower_pick(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    try:
        slot = int(callback.data.split(":")[2])
        if slot not in (0, 1, 2):
            raise ValueError
    except (IndexError, ValueError):
        await callback.answer()
        return

    round_repo = ChatGameRoundRepository(session)
    round_ = await round_repo.get_active(callback.message.chat.id, callback.from_user.id, "tower")
    if round_ is None:
        await callback.answer("⚠️ Раунд не найден или уже завершён.", show_alert=True)
        return

    state = round_repo.load_state(round_)
    mines: list[list[int]] = state["mines"]
    history: list[int] = state["history"] + [slot]
    level = round_.level
    max_levels = len(mines)
    bet = float(round_.bet)

    if slot in mines[level]:
        await round_repo.delete(round_)
        await record_result(
            session, callback.from_user.id, callback.message.chat.id, "tower",
            bet, 0.0, "chat_tower_total_bet", "chat_tower_total_payout",
        )
        text = (
            f"💣 <b>Мина! Башня рухнула.</b>\n\n"
            f"Уровень: <b>{level + 1}/{max_levels}</b>\n"
            f"Ставка сгорела: -{bet:.2f} ⭐"
        )
        kb = tower_playing_kb(max_levels, mines, history, active_level=None, coeff=0.0, payout=0.0)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
        return

    new_level = level + 1
    coeff = await get_chat_tower_coeff(session, level)
    payout = round(bet * coeff, 2)

    if new_level >= max_levels:
        await round_repo.delete(round_)
        await credit_stars(session, callback.from_user.id, Decimal(str(payout)))
        await record_result(
            session, callback.from_user.id, callback.message.chat.id, "tower",
            bet, payout, "chat_tower_total_bet", "chat_tower_total_payout",
        )
        text = (
            f"🎉 <b>Башня покорена!</b>\n\n"
            f"Уровень: <b>{max_levels}/{max_levels}</b>\n"
            f"Множитель: <b>×{coeff:.2f}</b>\n"
            f"Выигрыш: <b>+{payout:.2f} ⭐</b>"
        )
        kb = tower_playing_kb(max_levels, mines, history, active_level=None, coeff=0.0, payout=0.0)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
        return

    state["history"] = history
    await round_repo.save_state(round_, state, level=new_level)

    text = (
        f"🗼 <b>Башня</b>\n\nСтавка: <b>{bet:.2f} ⭐</b> | Уровень: <b>{new_level + 1}/{max_levels}</b>\n"
        f"Множитель: <b>×{coeff:.2f}</b> | Возможный выигрыш: <b>{payout:.2f} ⭐</b>\n\n"
        "Выбери плитку без мины:"
    )
    kb = tower_playing_kb(max_levels, mines, history, active_level=new_level, coeff=coeff, payout=payout)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "chattower:cashout")
async def cb_tower_cashout(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    round_repo = ChatGameRoundRepository(session)
    round_ = await round_repo.get_active(callback.message.chat.id, callback.from_user.id, "tower")
    if round_ is None or round_.level == 0:
        await callback.answer("⚠️ Сначала пройди хотя бы один уровень.", show_alert=True)
        return

    state = round_repo.load_state(round_)
    mines: list[list[int]] = state["mines"]
    history: list[int] = state["history"]
    max_levels = len(mines)
    coeff = await get_chat_tower_coeff(session, round_.level - 1)
    bet = float(round_.bet)
    payout = round(bet * coeff, 2)

    await round_repo.delete(round_)
    await credit_stars(session, callback.from_user.id, Decimal(str(payout)))
    await record_result(
        session, callback.from_user.id, callback.message.chat.id, "tower",
        bet, payout, "chat_tower_total_bet", "chat_tower_total_payout",
    )

    text = (
        f"💰 <b>Выигрыш забран!</b>\n\nПройдено уровней: <b>{round_.level}/{max_levels}</b>\n"
        f"Множитель: <b>×{coeff:.2f}</b>\nВыплата: <b>+{payout:.2f} ⭐</b>"
    )
    kb = tower_playing_kb(max_levels, mines, history, active_level=None, coeff=0.0, payout=0.0)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "chattower:noop")
async def cb_tower_noop(callback: CallbackQuery) -> None:
    await callback.answer()
