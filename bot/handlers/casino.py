from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.keyboards.games import casino_menu_kb
from bot.services.game_abandon_guard import guard_active_game

router = Router()


@router.callback_query(lambda c: c.data == "menu:casino")
async def cb_casino_menu(
    callback: CallbackQuery, db_user: User, state: FSMContext, session: AsyncSession,
) -> None:
    if await guard_active_game(callback, session, db_user, state):
        return
    await state.clear()
    text = (
        f"🎰 <b>Казино</b>\n\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>\n\n"
        "Выбери игру:"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=casino_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=casino_menu_kb())
    await callback.answer()
