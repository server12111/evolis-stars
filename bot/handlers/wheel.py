import math

from aiogram import Router, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, GameSession
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.random_game import try_consume_free_game_credit
from bot.services.casino import get_wheel_outcome, update_casino_profit
from bot.keyboards.wheel import wheel_menu_kb, wheel_bet_kb, wheel_cancel_kb, wheel_result_kb
from bot.states.games import WheelStates

router = Router()


@router.callback_query(lambda c: c.data == "menu:wheel")
async def cb_wheel_menu(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    if not await SettingsRepository(session).get_bool("game_wheel_enabled", True):
        await callback.answer("🎡 Игра временно отключена.", show_alert=True)
        return
    text = (
        "🎡 <b>Все или ничего</b>\n\n"
        "Два исхода:\n"
        "• <b>0.1x</b> — потеря 90% ставки\n"
        "• <b>50x</b> — джекпот!\n\n"
        "Выбери ставку и испытай удачу!"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=wheel_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=wheel_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "wheel:choose_bet")
async def cb_wheel_choose_bet(callback: CallbackQuery) -> None:
    try:
        await callback.message.edit_text("🎡 <b>Выбери ставку:</b>", parse_mode="HTML", reply_markup=wheel_bet_kb())
    except Exception:
        await callback.message.answer("🎡 <b>Выбери ставку:</b>", parse_mode="HTML", reply_markup=wheel_bet_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("wheel:bet:") and c.data != "wheel:bet:custom")
async def cb_wheel_bet(callback: CallbackQuery, session: AsyncSession, bot: Bot, db_user: User) -> None:
    if not await SettingsRepository(session).get_bool("game_wheel_enabled", True):
        await callback.answer("🎡 Игра временно отключена.", show_alert=True)
        return
    try:
        bet = float(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверная ставка.", show_alert=True)
        return
    free = await try_consume_free_game_credit(session, db_user, bet)
    if not free and db_user.stars_balance < bet:
        await callback.answer("❌ Недостаточно звёзд.", show_alert=True)
        return
    await _spin_and_send(None, callback, session, bot, db_user, bet, free=free)


@router.callback_query(lambda c: c.data == "wheel:bet:custom")
async def cb_wheel_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WheelStates.entering_bet)
    try:
        await callback.message.edit_text("🎡 Введи сумму ставки (мин. 1 RP⭐️):", reply_markup=wheel_cancel_kb())
    except Exception:
        await callback.message.answer("🎡 Введи сумму ставки:", reply_markup=wheel_cancel_kb())
    await callback.answer()


@router.message(WheelStates.entering_bet)
async def msg_wheel_bet(message: Message, state: FSMContext, session: AsyncSession, bot: Bot, db_user: User) -> None:
    try:
        bet = float(message.text.strip().replace(",", "."))
        if not math.isfinite(bet) or bet < 1:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("⚠️ Введи число 1 или больше.", reply_markup=wheel_cancel_kb())
        return
    await state.clear()
    if not await SettingsRepository(session).get_bool("game_wheel_enabled", True):
        await message.answer("🎡 Игра временно отключена.")
        return
    max_bet = await SettingsRepository(session).get_float("wheel_max_bet", 500.0)
    if bet > max_bet:
        await message.answer(f"❌ Макс. ставка: <b>{max_bet:.0f} RP⭐️</b>", parse_mode="HTML", reply_markup=wheel_cancel_kb())
        return
    free = await try_consume_free_game_credit(session, db_user, bet)
    if not free and db_user.stars_balance < bet:
        await message.answer("❌ Недостаточно звёзд.", reply_markup=wheel_cancel_kb())
        return
    await _spin_and_send(message, None, session, bot, db_user, bet, free=free)


async def _spin_and_send(
    message, callback, session: AsyncSession, bot: Bot, user: User, bet: float, free: bool = False,
) -> None:
    coeff = await get_wheel_outcome(session)
    payout = round(bet * coeff, 2)

    user.stars_balance = round(float(user.stars_balance) + (0 if free else -bet) + payout, 2)
    session.add(GameSession(
        user_id=user.user_id, game_type="wheel",
        bet=bet, payout=payout, result="win" if payout > bet else "lose",
    ))
    await update_casino_profit(session, "wheel", bet, payout)
    await session.commit()

    # Load video from settings
    s_repo = SettingsRepository(session)
    video_key = "wheel_video_50x" if coeff == 50.0 else "wheel_video_01x"
    video_file_id = await s_repo.get(video_key)

    net = round(payout - bet, 2)
    sign = "+" if net >= 0 else ""
    if coeff == 50.0:
        result_text = (
            f"🎉 <b>ДЖЕКПОТ! 50x!</b>\n\n"
            f"Ставка: {bet} RP⭐️\n"
            f"Выигрыш: <b>{payout} RP⭐️</b> ({sign}{net} RP⭐️)\n"
            f"💰 Баланс: <b>{float(user.stars_balance):.2f} RP⭐️</b>"
        )
    else:
        result_text = (
            f"😔 <b>0.1x — не повезло</b>\n\n"
            f"Ставка: {bet} RP⭐️\n"
            f"Вернулось: <b>{payout} RP⭐️</b> ({sign}{net} RP⭐️)\n"
            f"💰 Баланс: <b>{float(user.stars_balance):.2f} RP⭐️</b>"
        )

    chat_id = user.user_id
    if video_file_id:
        try:
            if callback:
                await callback.message.delete()
            await bot.send_video(
                chat_id=chat_id,
                video=video_file_id,
                caption=result_text,
                parse_mode="HTML",
                reply_markup=wheel_result_kb(),
            )
        except Exception:
            await bot.send_message(chat_id, result_text, parse_mode="HTML", reply_markup=wheel_result_kb())
    else:
        if callback:
            try:
                await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=wheel_result_kb())
            except Exception:
                await bot.send_message(chat_id, result_text, parse_mode="HTML", reply_markup=wheel_result_kb())
        else:
            await message.answer(result_text, parse_mode="HTML", reply_markup=wheel_result_kb())

    if callback:
        await callback.answer()
