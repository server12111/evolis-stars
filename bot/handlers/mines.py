import math
import random

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, GameSession
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.random_game import has_free_game_credit_for, try_consume_free_game_credit
from bot.services.mines import mines_coeff, get_mines_params
from bot.services.house_edge import is_in_recovery
from bot.keyboards.mines import (
    mines_bet_kb, mines_count_kb, mines_playing_kb, mines_over_kb, mines_cancel_kb,
)
from bot.states.games import MinesStates

router = Router()


def _make_board(mines_count: int) -> list[int]:
    board = [0] * 25
    for pos in random.sample(range(25), mines_count):
        board[pos] = 1
    return board


@router.callback_query(lambda c: c.data == "menu:mines")
async def cb_mines_menu(callback: CallbackQuery, db_user: User, state: FSMContext, session: AsyncSession) -> None:
    # Guard: don't interrupt active game
    current = await state.get_state()
    if current == MinesStates.playing:
        await callback.answer("⚠️ У вас уже есть активная игра! Завершите её.", show_alert=True)
        return

    await state.clear()
    s_repo = SettingsRepository(session)
    if not await s_repo.get_bool("mines_enabled", True):
        await callback.answer("💣 Мины временно недоступны.", show_alert=True)
        return

    min_bet = await s_repo.get_float("mines_min_bet", 1.0)
    text = (
        f"💣 <b>Мины</b>\n\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>\n"
        f"Мин. ставка: <b>{min_bet:.0f} ⭐</b>\n\n"
        "Выбери ставку:"
    )
    await state.set_state(MinesStates.choose_bet)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=mines_bet_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=mines_bet_kb())
    await callback.answer()


@router.callback_query(MinesStates.choose_bet, lambda c: c.data and c.data.startswith("mines:bet:") and c.data != "mines:bet:custom")
async def cb_mines_bet(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    try:
        bet = float(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверная ставка.", show_alert=True)
        return
    await _ask_mines_count(callback, state, session, db_user, bet)


@router.callback_query(MinesStates.choose_bet, lambda c: c.data == "mines:bet:custom")
async def cb_mines_bet_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MinesStates.choose_bet)
    await state.update_data(awaiting_custom=True)
    try:
        await callback.message.edit_text("💣 Введи сумму ставки:", reply_markup=mines_cancel_kb())
    except Exception:
        await callback.message.answer("💣 Введи сумму ставки:", reply_markup=mines_cancel_kb())
    await callback.answer()


@router.message(MinesStates.choose_bet)
async def msg_mines_bet_custom(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    try:
        bet = float(message.text.strip().replace(",", "."))
        if not math.isfinite(bet) or bet <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:", reply_markup=mines_cancel_kb())
        return

    s_repo = SettingsRepository(session)
    min_bet = await s_repo.get_float("mines_min_bet", 1.0)
    if bet < min_bet:
        await message.answer(f"❌ Мин. ставка: <b>{min_bet:.0f} ⭐</b>", parse_mode="HTML", reply_markup=mines_cancel_kb())
        return

    max_bet = await s_repo.get_float("mines_max_bet", 500.0)
    if bet > max_bet:
        await message.answer(f"❌ Макс. ставка: <b>{max_bet:.0f} ⭐</b>", parse_mode="HTML", reply_markup=mines_cancel_kb())
        return

    if float(db_user.stars_balance) < bet and not await has_free_game_credit_for(session, db_user, bet):
        await message.answer("❌ Недостаточно звёзд.", reply_markup=mines_cancel_kb())
        return

    await state.set_state(MinesStates.choose_mines)
    await state.update_data(bet=bet)
    await message.answer(
        f"💣 Ставка: <b>{bet:.2f} ⭐</b>\nВыбери количество мин:",
        parse_mode="HTML",
        reply_markup=mines_count_kb(),
    )


async def _ask_mines_count(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User, bet: float) -> None:
    s_repo = SettingsRepository(session)
    min_bet = await s_repo.get_float("mines_min_bet", 1.0)
    if bet < min_bet:
        await callback.answer(f"❌ Мин. ставка: {min_bet:.0f} ⭐", show_alert=True)
        return
    max_bet = await s_repo.get_float("mines_max_bet", 500.0)
    if bet > max_bet:
        await callback.answer(f"❌ Макс. ставка: {max_bet:.0f} ⭐", show_alert=True)
        return
    if float(db_user.stars_balance) < bet and not await has_free_game_credit_for(session, db_user, bet):
        await callback.answer("❌ Недостаточно звёзд.", show_alert=True)
        return

    await state.set_state(MinesStates.choose_mines)
    await state.update_data(bet=bet)
    text = f"💣 Ставка: <b>{bet:.2f} ⭐</b>\nВыбери количество мин:"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=mines_count_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=mines_count_kb())
    await callback.answer()


@router.callback_query(MinesStates.choose_mines, lambda c: c.data and c.data.startswith("mines:count:"))
async def cb_mines_count(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    try:
        mines_count = int(callback.data.split(":")[2])
        if mines_count not in (3, 5, 10, 15):
            raise ValueError
    except (IndexError, ValueError):
        await callback.answer("❌ Неверное значение.", show_alert=True)
        return

    data = await state.get_data()
    bet = data["bet"]

    free = await try_consume_free_game_credit(session, db_user, bet)
    if not free and float(db_user.stars_balance) < bet:
        await callback.answer("❌ Недостаточно звёзд.", show_alert=True)
        await state.clear()
        return

    # Deduct bet (unless it was covered by a free-game credit)
    if not free:
        db_user.stars_balance = round(float(db_user.stars_balance) - bet, 2)
    await session.commit()

    board = _make_board(mines_count)
    in_recovery = await is_in_recovery(session, "mines")
    await state.set_state(MinesStates.playing)
    await state.update_data(
        bet=bet, mines_count=mines_count, board=board, opened=[], gems=0, in_recovery=in_recovery,
    )

    house_edge, max_coeff = await get_mines_params(session, punish=in_recovery)
    coeff = mines_coeff(mines_count, 0, house_edge, max_coeff)
    payout = round(bet * coeff, 2)

    text = (
        f"💣 <b>Мины</b> — {mines_count} мин на поле 5×5\n\n"
        f"Ставка: <b>{bet:.2f} ⭐</b>\n"
        f"Текущий коэф: <b>×{coeff:.2f}</b>\n"
        f"Нажми ⬜ чтобы открыть клетку. Избегай 💣!"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=mines_playing_kb(board, [], coeff, payout),
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="HTML",
            reply_markup=mines_playing_kb(board, [], coeff, payout),
        )
    await callback.answer()


@router.callback_query(MinesStates.playing, lambda c: c.data and c.data.startswith("mines:open:"))
async def cb_mines_open(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    try:
        idx = int(callback.data.split(":")[2])
        if not (0 <= idx < 25):
            raise ValueError
    except (IndexError, ValueError):
        await callback.answer()
        return

    data = await state.get_data()
    board = data["board"]
    opened = data["opened"]
    bet = data["bet"]
    mines_count = data["mines_count"]
    gems = data.get("gems", 0)

    if idx in opened:
        await callback.answer()
        return

    opened = opened + [idx]

    if board[idx] == 1:
        # Hit a mine — game over
        session.add(GameSession(
            user_id=db_user.user_id, game_type="mines",
            bet=bet, payout=0, result="lose",
        ))
        s_repo = SettingsRepository(session)
        await s_repo.add_float("mines_total_bet", bet)
        await s_repo.add_float("mines_total_payout", 0)
        await session.commit()
        await state.clear()

        text = (
            f"💥 <b>Мина! Игра окончена.</b>\n\n"
            f"Ставка: <b>{bet:.2f} ⭐</b>\n"
            f"Потеря: <b>-{bet:.2f} ⭐</b>\n"
            f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>"
        )
        try:
            await callback.message.edit_text(
                text, parse_mode="HTML",
                reply_markup=mines_over_kb(board, opened),
            )
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=mines_over_kb(board, opened))
    else:
        gems = gems + 1
        house_edge, max_coeff = await get_mines_params(session, punish=data.get("in_recovery", False))
        coeff = mines_coeff(mines_count, gems, house_edge, max_coeff)
        payout = round(bet * coeff, 2)

        await state.update_data(opened=opened, gems=gems)

        text = (
            f"💣 <b>Мины</b> — {mines_count} мин\n\n"
            f"Ставка: <b>{bet:.2f} ⭐</b>\n"
            f"Открыто: <b>{gems}</b> 💎 | Коэф: <b>×{coeff:.2f}</b>\n"
            f"Потенциальный выигрыш: <b>{payout:.2f} ⭐</b>"
        )
        try:
            await callback.message.edit_text(
                text, parse_mode="HTML",
                reply_markup=mines_playing_kb(board, opened, coeff, payout),
            )
        except Exception:
            pass

    await callback.answer()


@router.callback_query(MinesStates.playing, lambda c: c.data == "mines:cashout")
async def cb_mines_cashout(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    data = await state.get_data()
    bet = data["bet"]
    mines_count = data["mines_count"]
    gems = data.get("gems", 0)
    board = data["board"]
    opened = data["opened"]

    if gems == 0:
        await callback.answer("⚠️ Сначала открой хотя бы одну клетку!", show_alert=True)
        return

    house_edge, max_coeff = await get_mines_params(session, punish=data.get("in_recovery", False))
    coeff = mines_coeff(mines_count, gems, house_edge, max_coeff)
    payout = round(bet * coeff, 2)

    db_user.stars_balance = round(float(db_user.stars_balance) + payout, 2)
    session.add(GameSession(
        user_id=db_user.user_id, game_type="mines",
        bet=bet, payout=payout, result="win",
    ))
    s_repo = SettingsRepository(session)
    await s_repo.add_float("mines_total_bet", bet)
    await s_repo.add_float("mines_total_payout", payout)
    await session.commit()
    await state.clear()

    net = round(payout - bet, 2)
    text = (
        f"💰 <b>Выигрыш забран!</b>\n\n"
        f"Ставка: <b>{bet:.2f} ⭐</b>\n"
        f"Открыто: <b>{gems}</b> 💎 | Коэф: <b>×{coeff:.2f}</b>\n"
        f"Выигрыш: <b>+{payout:.2f} ⭐</b> (прибыль: +{net:.2f} ⭐)\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=mines_over_kb(board, opened))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=mines_over_kb(board, opened))
    await callback.answer()


@router.callback_query(MinesStates.playing, lambda c: c.data == "mines:quit")
async def cb_mines_quit(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    data = await state.get_data()
    bet = data.get("bet", 0)
    gems = data.get("gems", 0)
    if gems == 0:
        # No gems opened — just refund and exit
        db_user.stars_balance = round(float(db_user.stars_balance) + bet, 2)
        await session.commit()
        await state.clear()
        await callback.answer("↩️ Ставка возвращена.")
    else:
        await callback.answer("⚠️ Сначала заберите выигрыш или откройте мину!", show_alert=True)
        return

    from bot.keyboards.main import main_menu_kb
    try:
        await callback.message.edit_text(
            f"🏠 <b>Главное меню</b>\n\n💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>",
            parse_mode="HTML", reply_markup=main_menu_kb(),
        )
    except Exception:
        await callback.message.answer(
            "🏠 <b>Главное меню</b>",
            parse_mode="HTML", reply_markup=main_menu_kb(),
        )


@router.callback_query(lambda c: c.data == "mines:noop")
async def cb_mines_noop(callback: CallbackQuery) -> None:
    await callback.answer()
