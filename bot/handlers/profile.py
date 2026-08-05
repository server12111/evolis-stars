from html import escape

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.promo import PromoRepository
from bot.keyboards.main import back_to_menu_kb
from bot.keyboards.tos import tos_view_kb
from bot.services.tos import get_tos_urls
from bot.states.promo import PromoStates

router = Router()
settings = get_settings()


def profile_kb() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎁 Промокод", callback_data="profile:promo"))
    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить в группу",
            url=(
                f"https://t.me/{settings.bot_username}?startgroup=owner"
                # Per https://core.telegram.org/api/links, admin= rights
                # are joined with "+", not commas — commas only partially
                # worked (confirmed live), which is why this used to be
                # comma-separated.
                "&admin=change_info+delete_messages+invite_users+restrict_members+"
                "pin_messages+manage_topics+promote_members+manage_video_chats+anonymous+manage_chat"
            ),
        )
    )
    builder.row(InlineKeyboardButton(text="📜 Соглашение и политика", callback_data="profile:tos"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder


@router.callback_query(lambda c: c.data == "menu:profile")
async def cb_profile(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    repo = ContentRepository(session)
    template = await repo.get_text("profile")
    photo = await repo.get_photo("profile")

    username_display = escape(f"@{db_user.username}") if db_user.username else "не установлен"
    first_name = escape(db_user.first_name)

    try:
        text = template.format(
            name=first_name,
            user_id=db_user.user_id,
            username=username_display,
            balance=f"{float(db_user.stars_balance):.2f}",
            referrals=db_user.referrals_count,
        )
    except (KeyError, ValueError):
        text = (
            f"👤 <b>Профиль</b>\n\n"
            f"Имя: <b>{first_name}</b>\n"
            f"ID: <code>{db_user.user_id}</code>\n"
            f"Username: {username_display}\n\n"
            f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>\n"
            f"👥 Рефералов: <b>{db_user.referrals_count}</b>"
        )

    kb = profile_kb().as_markup()
    try:
        if photo:
            await callback.message.delete()
            await callback.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(lambda c: c.data == "profile:tos")
async def cb_profile_tos(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = ContentRepository(session)
    text = await repo.get_text("tos")
    user_agreement_url, privacy_policy_url = await get_tos_urls(session)
    kb = tos_view_kb(user_agreement_url, privacy_policy_url)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(lambda c: c.data == "profile:promo")
async def cb_promo_enter(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PromoStates.enter_code)
    cancel_kb = InlineKeyboardBuilder()
    cancel_kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:profile"))
    try:
        await callback.message.edit_text(
            "🎁 <b>Активация промокода</b>\n\nВведи промокод:",
            parse_mode="HTML",
            reply_markup=cancel_kb.as_markup(),
        )
    except Exception:
        await callback.message.answer(
            "🎁 <b>Активация промокода</b>\n\nВведи промокод:",
            parse_mode="HTML",
            reply_markup=cancel_kb.as_markup(),
        )
    await callback.answer()


@router.message(PromoStates.enter_code)
async def msg_promo_code(message: Message, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    code = (message.text or "").strip().upper()
    if not code:
        await message.answer("❌ Введи код.", reply_markup=back_to_menu_kb())
        return

    p_repo = PromoRepository(session)
    promo = await p_repo.get_by_code(code)

    if not promo or not promo.is_active:
        await message.answer("❌ Промокод не найден или неактивен.", reply_markup=back_to_menu_kb())
        return

    from datetime import datetime
    if promo.expires_at and promo.expires_at < datetime.utcnow():
        await message.answer("❌ Срок действия промокода истёк.", reply_markup=back_to_menu_kb())
        return

    if promo.usage_limit > 0 and promo.used_count >= promo.usage_limit:
        await message.answer("❌ Лимит использований исчерпан.", reply_markup=back_to_menu_kb())
        return

    already = await p_repo.already_used(promo.id, db_user.user_id)
    if already:
        await message.answer("❌ Вы уже использовали этот промокод.", reply_markup=back_to_menu_kb())
        return

    used = await p_repo.use(promo, db_user.user_id)
    if used:
        db_user.stars_balance += promo.reward_amount
        await session.commit()
        await message.answer(
            f"✅ <b>Промокод активирован!</b>\n\n"
            f"Вам начислено: <b>+{float(promo.reward_amount):.2f} ⭐</b>\n"
            f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
    else:
        await message.answer("❌ Не удалось активировать промокод.", reply_markup=back_to_menu_kb())
