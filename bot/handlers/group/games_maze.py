import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.repositories.chat_game import ChatGameRoundRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.group.games_maze import maze_playing_kb
from bot.keyboards.group.registration import registration_required_kb
from bot.services.chat_eligibility import credit_stars
from bot.services.chat_games import (
    MAZE_JACKPOT_BONUS,
    MAZE_MAX_SHIELDS,
    MAZE_TREASURE_BONUS,
    get_maze_params,
    maze_base_coeff,
    maze_draw_tile,
    place_bet,
    record_result,
)
from bot.services.house_edge import is_in_recovery

settings = get_settings()

router = Router()

_START_PATTERN = re.compile(r"^лабиринт\s+(\d+(?:[.,]\d+)?)\s*$", re.IGNORECASE)


def _matches_maze_start(message: Message) -> bool:
    return bool(message.text and _START_PATTERN.match(message.text.strip()))


def _current_multiplier(step: int, bonus: float, house_edge: float, max_coeff: float) -> float:
    return round(maze_base_coeff(step, house_edge, max_coeff) + bonus, 4)


def _status_text(bet: float, step: int, shields: int, multiplier: float, extra: str = "") -> str:
    payout = round(bet * multiplier, 2)
    return (
        f"🌀 <b>Лабиринт</b>\n\n"
        f"Ставка: <b>{bet:.2f} ⭐</b> | Шаг: <b>{step}</b>\n"
        f"🛡 Щитов: <b>{shields}</b>\n"
        f"Множитель: <b>×{multiplier:.2f}</b> | Возможный выигрыш: <b>{payout:.2f} ⭐</b>"
        f"{extra}"
    )


@router.message(_matches_maze_start)
async def msg_maze_start(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    match = _START_PATTERN.match(message.text.strip())
    bet = float(match.group(1).replace(",", "."))

    settings_repo = SettingsRepository(session)
    if not await settings_repo.get_bool("maze_enabled", True):
        await message.reply("🌀 Лабиринт временно недоступен.")
        return

    round_repo = ChatGameRoundRepository(session)
    existing = await round_repo.get_active(message.chat.id, message.from_user.id, "maze")
    if existing:
        await message.reply("⚠️ У тебя уже есть активный лабиринт в этом чате.")
        return

    min_bet = await settings_repo.get_float("maze_min_bet", 1.0)
    max_bet = await settings_repo.get_float("maze_max_bet", 500.0)
    ok, error, needs_registration = await place_bet(session, message.from_user.id, bet, min_bet, max_bet)
    if not ok:
        kb = registration_required_kb(settings.bot_username) if needs_registration else None
        await message.reply(error, reply_markup=kb)
        return

    in_recovery = await is_in_recovery(session, "maze")
    created = await round_repo.create(
        message.chat.id, message.from_user.id, "maze",
        bet, {"shields": 0, "bonus": 0.0, "in_recovery": in_recovery},
    )
    if created is None:
        await credit_stars(session, message.from_user.id, Decimal(str(bet)))
        await message.reply("⚠️ У тебя уже есть активный лабиринт в этом чате.")
        return

    text = _status_text(bet, 0, 0, 1.0, "\n\nВыбери «Идти дальше», чтобы сделать первый шаг.")
    await message.reply(text, parse_mode="HTML", reply_markup=maze_playing_kb(can_cashout=False))


@router.callback_query(F.data == "maze:continue")
async def cb_maze_continue(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    round_repo = ChatGameRoundRepository(session)
    round_ = await round_repo.get_active(callback.message.chat.id, callback.from_user.id, "maze")
    if round_ is None:
        await callback.answer("⚠️ Раунд не найден или уже завершён.", show_alert=True)
        return

    state = round_repo.load_state(round_)
    tile = maze_draw_tile(punish=state.get("in_recovery", False))

    if tile == "trap":
        if state["shields"] > 0:
            state["shields"] -= 1
            note = "🛡 Ловушка! Щит поглотил удар."
        else:
            bet = float(round_.bet)
            if not await round_repo.delete(round_):
                await callback.answer("⚠️ Раунд не найден или уже завершён.", show_alert=True)
                return
            await record_result(
                session, callback.from_user.id, callback.message.chat.id, "maze",
                bet, 0.0, "maze_total_bet", "maze_total_payout",
            )
            text = f"💣 <b>Ловушка!</b> Без щита. Лабиринт разрушен.\n\nСтавка сгорела: -{bet:.2f} ⭐"
            try:
                await callback.message.edit_text(text, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, parse_mode="HTML")
            await callback.answer()
            return
    elif tile == "shield":
        state["shields"] = min(MAZE_MAX_SHIELDS, state["shields"] + 1)
        note = "🛡 Найден щит!"
    elif tile == "treasure":
        state["bonus"] = round(state["bonus"] + MAZE_TREASURE_BONUS, 4)
        note = "💰 Найдено сокровище!"
    elif tile == "jackpot":
        state["bonus"] = round(state["bonus"] + MAZE_JACKPOT_BONUS, 4)
        note = "💎 Джекпот!"
    else:
        note = "⭐️ Пустая клетка."

    new_step = round_.level + 1
    house_edge, max_coeff = await get_maze_params(session)
    multiplier = _current_multiplier(new_step, state["bonus"], house_edge, max_coeff)
    await round_repo.save_state(round_, state, level=new_step)

    text = _status_text(float(round_.bet), new_step, state["shields"], multiplier, f"\n{note}")
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=maze_playing_kb(can_cashout=True))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=maze_playing_kb(can_cashout=True))
    await callback.answer()


@router.callback_query(F.data == "maze:cashout")
async def cb_maze_cashout(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    round_repo = ChatGameRoundRepository(session)
    round_ = await round_repo.get_active(callback.message.chat.id, callback.from_user.id, "maze")
    if round_ is None or round_.level == 0:
        await callback.answer("⚠️ Сначала сделай хотя бы один шаг.", show_alert=True)
        return

    state = round_repo.load_state(round_)
    house_edge, max_coeff = await get_maze_params(session)
    multiplier = _current_multiplier(round_.level, state["bonus"], house_edge, max_coeff)
    bet = float(round_.bet)
    payout = round(bet * multiplier, 2)

    if not await round_repo.delete(round_):
        await callback.answer("⚠️ Раунд не найден или уже завершён.", show_alert=True)
        return
    await credit_stars(session, callback.from_user.id, Decimal(str(payout)))
    await record_result(
        session, callback.from_user.id, callback.message.chat.id, "maze",
        bet, payout, "maze_total_bet", "maze_total_payout",
    )

    text = (
        f"💰 <b>Выигрыш забран!</b>\n\nШагов пройдено: <b>{round_.level}</b>\n"
        f"Множитель: <b>×{multiplier:.2f}</b>\nВыплата: <b>+{payout:.2f} ⭐</b>"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()
