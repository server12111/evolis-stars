from aiogram import Router, Bot
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, GameSession
from bot.database.repositories.settings import SettingsRepository
from bot.services.casino import get_case_outcome, update_casino_profit, CASE_PRIZES
from bot.keyboards.cases import cases_menu_kb, case_confirm_kb, case_result_kb

router = Router()

_TIER_NAMES = {1: "Бронза 🥉", 3: "Серебро 🥈", 5: "Золото 🥇"}


@router.callback_query(lambda c: c.data == "menu:cases")
async def cb_cases_menu(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not await SettingsRepository(session).get_bool("game_cases_enabled", True):
        await callback.answer("🎁 Игра временно отключена.", show_alert=True)
        return
    text = (
        "🎁 <b>Кейсы</b>\n\n"
        "Открой кейс и получи приз!\n\n"
        "🥉 <b>Бронза (1 ⭐)</b> — призы до 3.5 ⭐\n"
        "🥈 <b>Серебро (3 ⭐)</b> — призы до 5 ⭐\n"
        "🥇 <b>Золото (5 ⭐)</b> — призы до 9 ⭐"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=cases_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=cases_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("cases:open:"))
async def cb_cases_open(callback: CallbackQuery) -> None:
    try:
        tier = int(callback.data.split(":")[2])
        if tier not in CASE_PRIZES:
            raise ValueError
    except (IndexError, ValueError):
        await callback.answer("Неверный кейс.", show_alert=True)
        return

    name = _TIER_NAMES[tier]
    top_prizes = " / ".join(f"{p}⭐" for p in CASE_PRIZES[tier][-3:])
    text = (
        f"🎁 <b>Кейс {name}</b>\n\n"
        f"Цена: <b>{tier} ⭐</b>\n"
        f"Макс. приз: <b>{CASE_PRIZES[tier][-1]} ⭐</b>\n"
        f"Примеры: {top_prizes}...\n\nОткрыть?"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=case_confirm_kb(tier))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=case_confirm_kb(tier))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("cases:confirm:"))
async def cb_cases_confirm(callback: CallbackQuery, session: AsyncSession, bot: Bot, db_user: User) -> None:
    if not await SettingsRepository(session).get_bool("game_cases_enabled", True):
        await callback.answer("🎁 Игра временно отключена.", show_alert=True)
        return
    try:
        tier = int(callback.data.split(":")[2])
        if tier not in CASE_PRIZES:
            raise ValueError
    except (IndexError, ValueError):
        await callback.answer("Неверный кейс.", show_alert=True)
        return

    if float(db_user.stars_balance) < tier:
        await callback.answer("❌ Недостаточно звёзд.", show_alert=True)
        return

    prize = await get_case_outcome(session, tier)
    payout = round(prize, 2)
    bet = float(tier)

    db_user.stars_balance = round(float(db_user.stars_balance) - bet + payout, 2)
    session.add(GameSession(
        user_id=db_user.user_id, game_type=f"case_{tier}",
        bet=bet, payout=payout, result="win" if payout > bet else "lose",
    ))
    await update_casino_profit(session, f"case_{tier}", bet, payout)
    await session.commit()

    s_repo = SettingsRepository(session)
    prize_str = str(payout).replace(".", "_")
    video_file_id = await s_repo.get(f"case_video_{prize_str}")

    name = _TIER_NAMES[tier]
    net = round(payout - bet, 2)
    sign = "+" if net >= 0 else ""

    if payout > bet:
        header = f"🎉 <b>Удача! Кейс {name}</b>"
    elif payout == bet:
        header = f"😐 <b>Ничья! Кейс {name}</b>"
    else:
        header = f"😔 <b>Не повезло. Кейс {name}</b>"

    result_text = (
        f"{header}\n\n"
        f"Цена: {tier} ⭐\n"
        f"Приз: <b>{payout} ⭐</b> ({sign}{net} ⭐)\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>"
    )

    if video_file_id:
        try:
            await callback.message.delete()
            await bot.send_video(
                chat_id=db_user.user_id,
                video=video_file_id,
                caption=result_text,
                parse_mode="HTML",
                reply_markup=case_result_kb(),
            )
        except Exception:
            await bot.send_message(db_user.user_id, result_text, parse_mode="HTML", reply_markup=case_result_kb())
    else:
        try:
            await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=case_result_kb())
        except Exception:
            await callback.message.answer(result_text, parse_mode="HTML", reply_markup=case_result_kb())

    await callback.answer()
