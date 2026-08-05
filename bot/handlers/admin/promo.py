import math

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, PromoCode
from bot.database.repositories.promo import PromoRepository
from bot.keyboards.admin.promo import promo_list_kb, promo_cancel_kb, promo_delete_confirm_kb
from bot.keyboards.admin.main import back_to_admin_kb
from bot.states.admin import AdminPromoStates
from bot.handlers.admin.stats import _is_admin

router = Router()


@router.callback_query(lambda c: c.data == "admin:promo")
async def cb_promo(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.clear()
    repo = PromoRepository(session)
    promos = await repo.all_active()
    text = f"🎟 <b>Промокоды</b>\n\nАктивных: <b>{len(promos)}</b>"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=promo_list_kb(promos))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=promo_list_kb(promos))
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:promo_new")
async def cb_promo_new(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.set_state(AdminPromoStates.enter_code)
    await callback.message.answer("✏️ Введи код промокода (латинские буквы/цифры):", reply_markup=promo_cancel_kb())
    await callback.answer()


@router.message(AdminPromoStates.enter_code)
async def msg_promo_code(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    code = (message.text or "").strip().upper()
    if not code:
        await message.answer("❌ Введи код:", reply_markup=promo_cancel_kb())
        return
    await state.update_data(code=code)
    await state.set_state(AdminPromoStates.enter_reward)
    await message.answer(f"✅ Код: <b>{code}</b>\n\nВведи награду (кол-во RP⭐️):", parse_mode="HTML", reply_markup=promo_cancel_kb())


@router.message(AdminPromoStates.enter_reward)
async def msg_promo_reward(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    try:
        reward = float(message.text.strip().replace(",", "."))
        if not math.isfinite(reward) or reward <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:", reply_markup=promo_cancel_kb())
        return
    await state.update_data(reward=reward)
    await state.set_state(AdminPromoStates.enter_limit)
    await message.answer(f"Награда: <b>{reward} RP⭐️</b>\n\nВведи лимит активаций (0 = неограниченно):", parse_mode="HTML", reply_markup=promo_cancel_kb())


@router.message(AdminPromoStates.enter_limit)
async def msg_promo_limit(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    try:
        limit = int(message.text.strip())
        if limit < 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи целое неотрицательное число:", reply_markup=promo_cancel_kb())
        return
    data = await state.get_data()
    await state.clear()

    repo = PromoRepository(session)
    promo = await repo.create(data["code"], data["reward"], usage_limit=limit)
    await message.answer(
        f"✅ <b>Промокод создан!</b>\n\nКод: <code>{promo.code}</code>\nНаграда: <b>{float(promo.reward_amount):.1f} RP⭐️</b>\nЛимит: <b>{limit or '∞'}</b>",
        parse_mode="HTML",
        reply_markup=back_to_admin_kb(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("admin:promo_del:"))
async def cb_promo_del(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    pid = int(callback.data.split(":")[2])
    promo = await session.get(PromoCode, pid)
    if not promo:
        await callback.answer("❌ Промокод не найден.", show_alert=True)
        return
    await callback.message.answer(
        f"🗑 Удалить промокод <b>{promo.code}</b>?",
        parse_mode="HTML",
        reply_markup=promo_delete_confirm_kb(pid),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:promo_del_confirm:"))
async def cb_promo_del_confirm(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    pid = int(callback.data.split(":")[2])
    repo = PromoRepository(session)
    deleted = await repo.delete(pid)
    await callback.answer("✅ Удалено" if deleted else "❌ Не найдено", show_alert=True)
    try:
        await callback.message.delete()
    except Exception:
        pass
