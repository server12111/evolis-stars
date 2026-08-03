from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.link_clicks import LinkButtonRepository
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.link_buttons import (
    link_button_cancel_kb,
    link_button_delete_confirm_kb,
    link_button_list_kb,
)
from bot.keyboards.admin.main import back_to_admin_kb
from bot.states.admin import AdminLinkButtonStates

router = Router()


@router.callback_query(lambda c: c.data == "admin:linkbtn")
async def cb_link_buttons(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    repo = LinkButtonRepository(session)
    buttons = await repo.all_active()
    text = (
        f"🔗 <b>Кнопки для рекламы в чатах</b>\n\n"
        f"Активных: <b>{len(buttons)}</b>\n\n"
        "Клики по каждой кнопке считаются уникально на пользователя — "
        "используются в объявлениях, которые бот постит в чатах с включённой рассылкой."
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=link_button_list_kb(buttons))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=link_button_list_kb(buttons))
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:linkbtn_new")
async def cb_link_button_new(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.set_state(AdminLinkButtonStates.enter_label)
    await callback.message.answer("✏️ Введи название кнопки (текст, который увидят пользователи):", reply_markup=link_button_cancel_kb())
    await callback.answer()


@router.message(AdminLinkButtonStates.enter_label)
async def msg_link_button_label(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    label = (message.text or "").strip()
    if not label:
        await message.answer("❌ Название не может быть пустым:", reply_markup=link_button_cancel_kb())
        return
    await state.update_data(label=label)
    await state.set_state(AdminLinkButtonStates.enter_url)
    await message.answer(f"Название: <b>{label}</b>\n\nВведи ссылку (https://...):", parse_mode="HTML", reply_markup=link_button_cancel_kb())


@router.message(AdminLinkButtonStates.enter_url)
async def msg_link_button_url(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    url = (message.text or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.answer("❌ Ссылка должна начинаться с http:// или https://:", reply_markup=link_button_cancel_kb())
        return
    data = await state.get_data()
    await state.clear()

    repo = LinkButtonRepository(session)
    button = await repo.create(data["label"], url, created_by=db_user.user_id)
    await message.answer(
        f"✅ <b>Кнопка создана!</b>\n\nНазвание: <b>{button.label}</b>\nСсылка: {button.destination_url}",
        parse_mode="HTML",
        reply_markup=back_to_admin_kb(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("admin:linkbtn_del:"))
async def cb_link_button_delete(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    link_id = int(callback.data.split(":")[2])
    button = await LinkButtonRepository(session).get(link_id)
    if not button:
        await callback.answer("❌ Не найдено.", show_alert=True)
        return
    await callback.message.answer(
        f"🗑 Удалить кнопку <b>{button.label}</b>?",
        parse_mode="HTML",
        reply_markup=link_button_delete_confirm_kb(link_id),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:linkbtn_del_confirm:"))
async def cb_link_button_delete_confirm(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    link_id = int(callback.data.split(":")[2])
    deleted = await LinkButtonRepository(session).delete(link_id)
    await callback.answer("✅ Удалено" if deleted else "❌ Не найдено", show_alert=True)
    try:
        await callback.message.delete()
    except Exception:
        pass
