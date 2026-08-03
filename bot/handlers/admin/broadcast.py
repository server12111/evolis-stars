from aiogram import Bot, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.link_clicks import LinkButtonRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.admin.broadcast import (
    broadcast_button_choice_kb,
    broadcast_button_pick_kb,
    broadcast_cancel_kb,
    broadcast_chat_audience_kb,
    broadcast_preview_kb,
    broadcast_scope_kb,
)
from bot.keyboards.admin.main import back_to_admin_kb
from bot.services.background import spawn_background
from bot.services.broadcast import broadcast as do_broadcast
from bot.states.admin import AdminBroadcastStates
from bot.handlers.admin.stats import _is_admin

router = Router()


def _target_description(scope: str, chat_audience: str | None) -> str:
    if scope == "bot":
        return "🤖 в боте (всем пользователям)"
    if chat_audience == "opted_in":
        return "💬 в чатах (только с включённой рассылкой)"
    return "💬 в чатах (всем)"


async def _build_reply_markup(session: AsyncSession, button_id: int | None) -> InlineKeyboardMarkup | None:
    if button_id is None:
        return None
    button = await LinkButtonRepository(session).get(button_id)
    if not button or not button.is_active:
        return None
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=button.label, callback_data=f"lc:{button.id}"))
    return builder.as_markup()


async def _run_broadcast(
    bot: Bot, admin_chat_id: int, target_ids: list[int], source_msg: Message,
    reply_markup: InlineKeyboardMarkup | None, target_label: str,
) -> None:
    """Runs outside the per-user lock (spawned as a background task) — a
    broadcast can take minutes at ~20 msg/s, and awaiting it inline inside
    the locked handler would block every other user sharing the admin's
    lock bucket for the whole duration."""
    success, fail = await do_broadcast(bot, target_ids, source_msg, reply_markup)
    try:
        await bot.send_message(
            admin_chat_id,
            f"✅ <b>Рассылка {target_label} завершена!</b>\n\n"
            f"✔️ Отправлено: <b>{success}</b>\n"
            f"❌ Ошибок: <b>{fail}</b>",
            parse_mode="HTML",
            reply_markup=back_to_admin_kb(),
        )
    except Exception:
        pass


@router.callback_query(lambda c: c.data == "admin:broadcast")
async def cb_broadcast(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.clear()
    text = "📣 <b>Рассылка</b>\n\nКуда отправить рассылку?"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=broadcast_scope_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=broadcast_scope_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:broadcast_scope:chats")
async def cb_broadcast_scope_chats(callback: CallbackQuery, db_user: User) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    text = "💬 <b>Рассылка в чатах</b>\n\nКаким чатам отправить?"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=broadcast_chat_audience_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=broadcast_chat_audience_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:broadcast_scope:bot")
async def cb_broadcast_scope_bot(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.update_data(scope="bot", chat_audience=None)
    await state.set_state(AdminBroadcastStates.waiting_message)
    text = "📣 <b>Рассылка в боте</b>\n\nОтправь сообщение (текст, фото, видео, документ или перешли сообщение):"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=broadcast_cancel_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=broadcast_cancel_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:broadcast_audience:"))
async def cb_broadcast_audience(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    audience = callback.data.rsplit(":", 1)[-1]
    await state.update_data(scope="chats", chat_audience=audience)
    await state.set_state(AdminBroadcastStates.waiting_message)
    text = "📣 <b>Рассылка в чатах</b>\n\nОтправь сообщение (текст, фото, видео, документ или перешли сообщение):"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=broadcast_cancel_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=broadcast_cancel_kb())
    await callback.answer()


@router.message(AdminBroadcastStates.waiting_message)
async def msg_broadcast_content(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user):
        return
    await state.update_data(message_id=message.message_id, chat_id=message.chat.id)
    await state.set_state(AdminBroadcastStates.choose_button)

    await message.answer(
        "👆 <b>Предпросмотр выше.</b>\n\nПрикрепить кнопку для кликов?",
        parse_mode="HTML",
        reply_markup=broadcast_button_choice_kb(),
    )


@router.callback_query(lambda c: c.data == "admin:broadcast_button:skip")
async def cb_broadcast_button_skip(callback: CallbackQuery, db_user: User, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.update_data(button_id=None)
    await _show_confirm(callback.message, state, session)
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:broadcast_button:attach")
async def cb_broadcast_button_attach(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    buttons = await LinkButtonRepository(session).all_active()
    text = "🔗 Выбери существующую кнопку или создай новую:"
    try:
        await callback.message.edit_text(text, reply_markup=broadcast_button_pick_kb(buttons))
    except Exception:
        await callback.message.answer(text, reply_markup=broadcast_button_pick_kb(buttons))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:broadcast_button_pick:"))
async def cb_broadcast_button_pick(callback: CallbackQuery, db_user: User, state: FSMContext, session: AsyncSession) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    button_id = int(callback.data.split(":")[2])
    await state.update_data(button_id=button_id)
    await _show_confirm(callback.message, state, session)
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:broadcast_button_new")
async def cb_broadcast_button_new(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminBroadcastStates.enter_button_label)
    await callback.message.answer("✏️ Введи название кнопки (текст, который увидят пользователи):", reply_markup=broadcast_cancel_kb())
    await callback.answer()


@router.message(AdminBroadcastStates.enter_button_label)
async def msg_broadcast_button_label(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user):
        return
    label = (message.text or "").strip()
    if not label:
        await message.answer("❌ Название не может быть пустым:", reply_markup=broadcast_cancel_kb())
        return
    await state.update_data(new_button_label=label)
    await state.set_state(AdminBroadcastStates.enter_button_url)
    await message.answer(f"Название: <b>{label}</b>\n\nВведи ссылку (https://...):", parse_mode="HTML", reply_markup=broadcast_cancel_kb())


@router.message(AdminBroadcastStates.enter_button_url)
async def msg_broadcast_button_url(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user):
        return
    url = (message.text or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.answer("❌ Ссылка должна начинаться с http:// или https://:", reply_markup=broadcast_cancel_kb())
        return
    data = await state.get_data()
    button = await LinkButtonRepository(session).create(data["new_button_label"], url, created_by=db_user.user_id)
    await state.update_data(button_id=button.id)
    await _show_confirm(message, state, session)


async def _show_confirm(sendable, state: FSMContext, session: AsyncSession) -> None:
    await state.set_state(AdminBroadcastStates.confirm)
    data = await state.get_data()
    target_line = _target_description(data.get("scope", "bot"), data.get("chat_audience"))
    button_id = data.get("button_id")
    button_line = ""
    if button_id is not None:
        button = await LinkButtonRepository(session).get(button_id)
        if button:
            button_line = f"\n🔗 Кнопка: <b>{button.label}</b>"

    text = (
        f"📣 <b>Подтверждение рассылки</b>\n\n"
        f"Куда: {target_line}{button_line}\n\n"
        "Отправить это сообщение?"
    )
    await sendable.answer(text, parse_mode="HTML", reply_markup=broadcast_preview_kb())


@router.callback_query(AdminBroadcastStates.confirm, lambda c: c.data == "admin:broadcast_confirm")
async def cb_broadcast_confirm(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    data = await state.get_data()
    await state.clear()

    source_msg = None
    try:
        source_msg = await callback.bot.forward_message(
            chat_id=callback.message.chat.id,
            from_chat_id=data["chat_id"],
            message_id=data["message_id"],
        )
    except Exception:
        await callback.answer("⚠️ Не удалось получить сообщение.", show_alert=True)
        return

    scope = data.get("scope", "bot")
    if scope == "chats":
        opted_in_only = data.get("chat_audience") == "opted_in"
        target_ids = await ChatRepository(session).all_active_chat_ids(opted_in_only=opted_in_only)
        target_label = "по чатам"
    else:
        target_ids = await UserRepository(session).all_active_ids()
        target_label = "по боту"

    reply_markup = await _build_reply_markup(session, data.get("button_id"))

    await callback.message.answer(
        f"⏳ Рассылка {target_label} для <b>{len(target_ids)}</b> получателей запущена в фоне — "
        "результат придёт отдельным сообщением.",
        parse_mode="HTML",
    )
    await callback.answer()

    spawn_background(
        _run_broadcast(callback.bot, callback.message.chat.id, target_ids, source_msg, reply_markup, target_label),
        name=f"broadcast:{db_user.user_id}",
    )
