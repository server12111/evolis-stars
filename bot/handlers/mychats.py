import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import Chat, ChatBroadcastMessage
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_broadcast import (
    MAX_BROADCAST_BUTTONS,
    MAX_BROADCAST_PHOTOS,
    ChatBroadcastRepository,
    load_buttons,
    load_photo_ids,
)
from bot.database.repositories.chat_promo import ChatPromoRepository
from bot.database.repositories.game import GameRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository
from bot.handlers.group.chat_bonus import (
    cb_bonus_add_sponsor_start,
    cb_bonus_mode,
    cb_bonus_sponsors_done,
    msg_bonus_code,
    msg_bonus_limit,
    msg_bonus_reward,
    start_bonus_creation,
)
from bot.handlers.group.chat_promo import msg_chat_promo_code, start_promo_creation
from bot.handlers.group.info import render_roulette_log_text, render_top_users_text
from bot.handlers.group.owner_menu import render_chat_panel_text
from bot.keyboards.mychats import (
    broadcast_ask_buttons_kb,
    broadcast_buttons_step_kb,
    broadcast_photos_step_kb,
    custom_broadcast_cancel_kb,
    custom_broadcast_panel_kb,
    mychat_back_kb,
    mychat_back_to_list_kb,
    mychat_panel_kb,
    mychats_list_kb,
)
from bot.services.content_moderation import find_banned_term
from bot.states.group import ChatOwnerBonusStates, ChatOwnerBroadcastStates, ChatOwnerPromoStates

router = Router()
logger = logging.getLogger(__name__)
settings = get_settings()

_GAME_LABELS = {"roulette": "Рулетка", "doors": "Двери", "maze": "Лабиринт", "tower": "Башня"}
_BROADCAST_MAX_TEXTS = 1
_BROADCAST_MAX_TEXT_LEN = 1000


def _parse_chat_id(callback: CallbackQuery) -> int | None:
    try:
        return int(callback.data.split(":")[-1])
    except (IndexError, ValueError, TypeError):
        return None


async def _verify_owned_chat(callback: CallbackQuery, session: AsyncSession, chat_id: int) -> Chat | None:
    """The one guard every panel action re-runs (item 6) — chat_id always
    comes from THIS callback's own data and is re-checked against the DB
    every single time, so a forged callback_data pointing at a chat this
    user doesn't own can never grant access, regardless of what the
    button claims."""
    chat = await ChatRepository(session).get(chat_id)
    if not chat or chat.owner_user_id != callback.from_user.id:
        logger.warning(
            "MYCHATS access denied: user=%s chat_id=%s (not owner)",
            callback.from_user.id, chat_id,
        )
        await callback.answer("❌ Этот чат вам не принадлежит.", show_alert=True)
        return None
    return chat


async def _render_panel(callback: CallbackQuery, session: AsyncSession, chat: Chat) -> None:
    has_promo = await ChatPromoRepository(session).get_by_chat(chat.chat_id) is not None
    text = await render_chat_panel_text(ChatRepository(session), chat.chat_id, chat.member_count)
    kb = mychat_panel_kb(chat.chat_id, chat.broadcast_opt_in, has_promo)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        if "message is not modified" in str(exc).lower():
            return
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "mychats:list")
async def cb_mychats_list(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.clear()
    chats = await ChatRepository(session).list_owned_by(callback.from_user.id)
    text = (
        "💬 <b>Панель чатов</b>\n\nВыберите чат для управления:"
        if chats else
        "💬 <b>Панель чатов</b>\n\nУ вас пока нет подключённых чатов."
    )
    kb = mychats_list_kb(chats, settings.bot_username)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("mychats:open:"))
async def cb_mychats_open(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.clear()
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return

    # Same member-count gate /EvolisOpen always enforced — connecting the
    # bot doesn't itself grant management access, only crossing the
    # threshold does. Preserved here rather than silently dropped.
    if chat.status != "active":
        min_members = await SettingsRepository(session).get_int("chat_min_members", 250)
        try:
            await callback.message.edit_text(
                f"⚠️ Управление чатом доступно от {min_members} участников. "
                f"Сейчас в чате: {chat.member_count}.",
                reply_markup=mychat_back_to_list_kb(),
            )
        except Exception:
            await callback.message.answer(
                f"⚠️ Управление чатом доступно от {min_members} участников. "
                f"Сейчас в чате: {chat.member_count}."
            )
        await callback.answer()
        return

    await _render_panel(callback, session, chat)
    await callback.answer()


@router.callback_query(F.data.startswith("mychats:refresh:"))
async def cb_mychats_refresh(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return

    try:
        member_count = await bot.get_chat_member_count(chat_id)
    except Exception as exc:
        logger.info("MYCHATS refresh: bot no longer accessible in chat %s: %s", chat_id, exc)
        await callback.answer("⚠️ Бот больше не в этом чате или недоступен.", show_alert=True)
        return

    min_members = await SettingsRepository(session).get_int("chat_min_members", 250)
    chat = await ChatRepository(session).upsert(
        chat_id=chat_id, title=chat.title, member_count=member_count,
        owner_user_id=chat.owner_user_id, min_members=min_members,
    )
    await _render_panel(callback, session, chat)
    await callback.answer("✅ Обновлено")


@router.callback_query(F.data.startswith("mychats:broadcast:"))
async def cb_mychats_broadcast_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return

    chat.broadcast_opt_in = not chat.broadcast_opt_in
    await session.commit()
    await _render_panel(callback, session, chat)
    await callback.answer("✅ Включено" if chat.broadcast_opt_in else "✅ Выключено")


@router.callback_query(F.data.startswith("mychats:stats:"))
async def cb_mychats_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return
    text = await render_chat_panel_text(ChatRepository(session), chat.chat_id, chat.member_count)
    kb = mychat_back_kb(chat_id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("mychats:top:"))
async def cb_mychats_top(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    if await _verify_owned_chat(callback, session, chat_id) is None:
        return
    text = await render_top_users_text(session, chat_id) or "Пока нет пользователей."
    kb = mychat_back_kb(chat_id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("mychats:log:"))
async def cb_mychats_log(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    if await _verify_owned_chat(callback, session, chat_id) is None:
        return
    text = await render_roulette_log_text(session, chat_id) or "Пока нет сыгранных партий в рулетку."
    kb = mychat_back_kb(chat_id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("mychats:games:"))
async def cb_mychats_games(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    if await _verify_owned_chat(callback, session, chat_id) is None:
        return
    stats = await GameRepository(session).chat_stats(chat_id)
    total = sum(stats.values())
    lines = [f"🎰 <b>Игры в чате</b>\n\nВсего сыграно: <b>{total}</b>\n"]
    for key, label in _GAME_LABELS.items():
        lines.append(f"{label}: <b>{stats.get(key, 0)}</b>")
    text = "\n".join(lines)
    kb = mychat_back_kb(chat_id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("mychats:promo:"))
async def cb_mychats_promo_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    if await _verify_owned_chat(callback, session, chat_id) is None:
        return
    await start_promo_creation(callback, session, state, chat_id)


@router.callback_query(F.data.startswith("mychats:bonus:"))
async def cb_mychats_bonus_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    if await _verify_owned_chat(callback, session, chat_id) is None:
        return
    await start_bonus_creation(callback, state, chat_id)


async def _broadcast_panel_content(session: AsyncSession, chat: Chat) -> tuple[str, object]:
    messages = await ChatBroadcastRepository(session).list_messages(chat.chat_id)
    min_interval = await SettingsRepository(session).get_int("broadcast_min_interval_seconds", 300)

    lines = [
        "📢 <b>Своя рассылка</b>\n",
        "Бот будет по очереди отправлять твои тексты прямо в этот чат — это "
        "бесплатная функция, которая помогает боту оставаться активным и "
        "заметным в чате.\n",
        f"Минимальный интервал: <b>{min_interval} сек.</b>",
    ]
    if messages:
        lines.append("\nТексты:")
        status_icons = {"pending": "⏳ на модерации", "approved": "✅ одобрен"}
        for i, message in enumerate(messages, start=1):
            preview = message.text if len(message.text) <= 60 else message.text[:57] + "..."
            extras = []
            photo_count = len(load_photo_ids(message))
            if photo_count:
                extras.append(f"📸{photo_count}")
            button_count = len(load_buttons(message))
            if button_count:
                extras.append(f"🔘{button_count}")
            extras_str = f" ({', '.join(extras)})" if extras else ""
            status = status_icons.get(message.status, message.status)
            lines.append(f"{i}. {preview}{extras_str} — {status}")
    else:
        lines.append("\nТекстов пока нет — добавь хотя бы один, чтобы включить рассылку.")

    interval = chat.custom_broadcast_interval_seconds
    lines.append(f"\nИнтервал: <b>{interval} сек.</b>" if interval else "\nИнтервал: <b>не задан</b>")
    lines.append(f"Статус: {'✅ включена' if chat.custom_broadcast_enabled else '❌ выключена'}")
    text = "\n".join(lines)
    kb = custom_broadcast_panel_kb(chat.chat_id, messages, chat.custom_broadcast_enabled)
    return text, kb


async def _render_broadcast_panel(callback: CallbackQuery, session: AsyncSession, chat: Chat) -> None:
    text, kb = await _broadcast_panel_content(session, chat)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


async def _send_broadcast_panel(message: Message, session: AsyncSession, chat: Chat) -> None:
    text, kb = await _broadcast_panel_content(session, chat)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("mychats:custombc:del:"))
async def cb_custom_broadcast_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    try:
        chat_id, message_id = int(parts[3]), int(parts[4])
    except (IndexError, ValueError):
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return
    await ChatBroadcastRepository(session).delete_message(chat_id, message_id)
    await _render_broadcast_panel(callback, session, chat)
    await callback.answer("✅ Удалено")


@router.callback_query(F.data.startswith("mychats:custombc:add:"))
async def cb_custom_broadcast_add_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return
    if await ChatBroadcastRepository(session).count(chat_id) >= _BROADCAST_MAX_TEXTS:
        await callback.answer(
            "❌ На один чат — один текст. Удали текущий, чтобы добавить новый.", show_alert=True,
        )
        return

    await state.set_state(ChatOwnerBroadcastStates.enter_text)
    await state.update_data(chat_id=chat_id)
    text = f"✏️ Отправь текст для рассылки (до {_BROADCAST_MAX_TEXT_LEN} символов):"
    try:
        await callback.message.edit_text(text, reply_markup=custom_broadcast_cancel_kb(chat_id))
    except Exception:
        await callback.message.answer(text, reply_markup=custom_broadcast_cancel_kb(chat_id))
    await callback.answer()


async def _verify_owned_chat_for_state(session: AsyncSession, data: dict, user_id: int) -> Chat | None:
    chat_id = data.get("chat_id")
    if chat_id is None:
        return None
    chat = await ChatRepository(session).get(chat_id)
    if chat is None or chat.owner_user_id != user_id:
        return None
    return chat


@router.message(ChatOwnerBroadcastStates.enter_text)
async def msg_custom_broadcast_text(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    chat = await _verify_owned_chat_for_state(session, data, message.from_user.id)
    if chat is None:
        await state.clear()
        return
    chat_id = chat.chat_id

    body = (message.text or "").strip()
    if not body:
        await message.answer("❌ Отправь текстовое сообщение:", reply_markup=custom_broadcast_cancel_kb(chat_id))
        return
    if len(body) > _BROADCAST_MAX_TEXT_LEN:
        await message.answer(
            f"❌ Слишком длинно (максимум {_BROADCAST_MAX_TEXT_LEN} символов):",
            reply_markup=custom_broadcast_cancel_kb(chat_id),
        )
        return
    if find_banned_term(body):
        logger.warning(
            "MYCHATS broadcast text rejected (banned content) chat=%s user=%s",
            chat_id, message.from_user.id,
        )
        await message.answer(
            "❌ Текст содержит запрещённый контент и не может быть использован. Отправь другой текст:",
            reply_markup=custom_broadcast_cancel_kb(chat_id),
        )
        return

    await state.update_data(pending_text=body, pending_photos=[])
    await state.set_state(ChatOwnerBroadcastStates.enter_photos)
    await message.answer(
        f"📸 Хочешь прикрепить фото (до {MAX_BROADCAST_PHOTOS} шт.)? Отправь их по одному, "
        "или сразу нажми «Без фото».",
        reply_markup=broadcast_photos_step_kb(chat_id, 0),
    )


@router.message(ChatOwnerBroadcastStates.enter_photos, F.photo)
async def msg_custom_broadcast_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    chat = await _verify_owned_chat_for_state(session, data, message.from_user.id)
    if chat is None:
        await state.clear()
        return
    chat_id = chat.chat_id

    photos = data.get("pending_photos") or []
    if len(photos) >= MAX_BROADCAST_PHOTOS:
        await message.answer(
            f"❌ Уже добавлено максимум {MAX_BROADCAST_PHOTOS} фото — нажми «Дальше».",
            reply_markup=broadcast_photos_step_kb(chat_id, len(photos)),
        )
        return

    photos = photos + [message.photo[-1].file_id]
    await state.update_data(pending_photos=photos)
    if len(photos) >= MAX_BROADCAST_PHOTOS:
        await message.answer(
            f"📸 Добавлено {len(photos)}/{MAX_BROADCAST_PHOTOS} фото — максимум. Нажми «Дальше».",
            reply_markup=broadcast_photos_step_kb(chat_id, len(photos)),
        )
        return
    await message.answer(
        f"📸 Добавлено {len(photos)}/{MAX_BROADCAST_PHOTOS} фото. Ещё фото или жми «Дальше».",
        reply_markup=broadcast_photos_step_kb(chat_id, len(photos)),
    )


@router.message(ChatOwnerBroadcastStates.enter_photos)
async def msg_custom_broadcast_photos_hint(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("chat_id")
    if chat_id is None:
        return
    photos = data.get("pending_photos") or []
    await message.answer(
        "❌ Отправь фото или нажми «Дальше».",
        reply_markup=broadcast_photos_step_kb(chat_id, len(photos)),
    )


@router.callback_query(ChatOwnerBroadcastStates.enter_photos, F.data.startswith("mychats:custombc:photos:next:"))
async def cb_custom_broadcast_photos_next(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    chat = await _verify_owned_chat_for_state(session, data, callback.from_user.id)
    if chat is None:
        await state.clear()
        await callback.answer()
        return

    await state.set_state(ChatOwnerBroadcastStates.ask_buttons)
    text = f"🔘 Добавить кнопки-ссылки к тексту (до {MAX_BROADCAST_BUTTONS} шт.)?"
    try:
        await callback.message.edit_text(text, reply_markup=broadcast_ask_buttons_kb(chat.chat_id))
    except Exception:
        await callback.message.answer(text, reply_markup=broadcast_ask_buttons_kb(chat.chat_id))
    await callback.answer()


async def _post_broadcast_for_moderation(
    bot: Bot, session: AsyncSession, chat: Chat, broadcast_msg: ChatBroadcastMessage,
) -> None:
    settings_repo = SettingsRepository(session)
    channel_id = settings.broadcast_moderation_channel_id or await settings_repo.get(
        "broadcast_moderation_channel_id",
    )
    if not channel_id:
        logger.warning(
            "Broadcast moderation channel not configured — message %s for chat %s stays pending indefinitely.",
            broadcast_msg.id, chat.chat_id,
        )
        return

    owner = await UserRepository(session).get(chat.owner_user_id)
    owner_display = f"@{owner.username}" if owner and owner.username else str(chat.owner_user_id)
    photos = load_photo_ids(broadcast_msg)
    buttons = load_buttons(broadcast_msg)
    extra_lines = []
    if photos:
        extra_lines.append(f"📸 Фото: {len(photos)}")
    if buttons:
        extra_lines.append("🔘 Кнопки:")
        extra_lines.extend(f"  • {escape(b['text'])} → {escape(b['url'])}" for b in buttons)
    extras = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    text = (
        f"📋 <b>Новый текст рассылки на модерацию</b>\n\n"
        f"💬 Чат: {escape(chat.title or str(chat.chat_id))} (<code>{chat.chat_id}</code>)\n"
        f"👤 Владелец: {owner_display} (<code>{chat.owner_user_id}</code>)\n\n"
        f"Текст:\n<code>{escape(broadcast_msg.text)}</code>"
        f"{extras}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"broadcast_mod:approve:{broadcast_msg.id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"broadcast_mod:reject:{broadcast_msg.id}"),
    ]])
    try:
        posted = await bot.send_message(int(channel_id), text, parse_mode="HTML", reply_markup=kb)
        await ChatBroadcastRepository(session).set_moderation_message_id(broadcast_msg.id, posted.message_id)
    except Exception as exc:
        logger.warning("Cannot post broadcast message %s for moderation: %s", broadcast_msg.id, exc)


async def _save_pending_broadcast(bot: Bot, session: AsyncSession, state: FSMContext, chat: Chat) -> None:
    data = await state.get_data()
    body = data.get("pending_text", "")
    photos = data.get("pending_photos") or []
    buttons = data.get("pending_buttons") or []
    broadcast_msg = await ChatBroadcastRepository(session).add_message(
        chat.chat_id, body, photos or None, buttons or None,
    )
    await state.clear()
    await _post_broadcast_for_moderation(bot, session, chat, broadcast_msg)


async def _finalize_broadcast_text(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    chat = await _verify_owned_chat_for_state(session, data, callback.from_user.id)
    if chat is None:
        await state.clear()
        await callback.answer()
        return

    await _save_pending_broadcast(callback.bot, session, state, chat)
    await _render_broadcast_panel(callback, session, chat)
    await callback.answer("⏳ Текст отправлен на модерацию.")


@router.callback_query(ChatOwnerBroadcastStates.ask_buttons, F.data.startswith("mychats:custombc:buttons:no:"))
async def cb_custom_broadcast_buttons_no(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _finalize_broadcast_text(callback, session, state)


@router.callback_query(ChatOwnerBroadcastStates.ask_buttons, F.data.startswith("mychats:custombc:buttons:yes:"))
async def cb_custom_broadcast_buttons_yes(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    chat = await _verify_owned_chat_for_state(session, data, callback.from_user.id)
    if chat is None:
        await state.clear()
        await callback.answer()
        return

    await state.update_data(pending_buttons=[])
    await state.set_state(ChatOwnerBroadcastStates.enter_button)
    text = (
        "🔘 Отправь кнопку в формате:\n<code>Текст кнопки - https://ссылка</code>"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=custom_broadcast_cancel_kb(chat.chat_id))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=custom_broadcast_cancel_kb(chat.chat_id))
    await callback.answer()


@router.message(ChatOwnerBroadcastStates.enter_button)
async def msg_custom_broadcast_button(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    chat = await _verify_owned_chat_for_state(session, data, message.from_user.id)
    if chat is None:
        await state.clear()
        return
    chat_id = chat.chat_id

    raw = (message.text or "").strip()
    label, _, url = raw.partition(" - ")
    label = label.strip()
    url = url.strip()
    if not label or not url or not url.startswith(("http://", "https://")):
        await message.answer(
            "❌ Неверный формат. Отправь так:\nТекст кнопки - https://ссылка",
            reply_markup=custom_broadcast_cancel_kb(chat_id),
        )
        return
    if len(label) > 40:
        await message.answer(
            "❌ Слишком длинный текст кнопки (максимум 40 символов):",
            reply_markup=custom_broadcast_cancel_kb(chat_id),
        )
        return
    if find_banned_term(label):
        logger.warning(
            "MYCHATS broadcast button rejected (banned content) chat=%s user=%s",
            chat_id, message.from_user.id,
        )
        await message.answer(
            "❌ Текст кнопки содержит запрещённый контент. Отправь другой:",
            reply_markup=custom_broadcast_cancel_kb(chat_id),
        )
        return

    buttons = (data.get("pending_buttons") or []) + [{"text": label, "url": url}]
    await state.update_data(pending_buttons=buttons)

    if len(buttons) >= MAX_BROADCAST_BUTTONS:
        await _save_pending_broadcast(message.bot, session, state, chat)
        await message.answer("⏳ Текст отправлен на модерацию.")
        await _send_broadcast_panel(message, session, chat)
        return
    await message.answer(
        f"🔘 Добавлено {len(buttons)}/{MAX_BROADCAST_BUTTONS} кнопок. Ещё кнопка или жми «Готово».",
        reply_markup=broadcast_buttons_step_kb(chat_id),
    )


@router.callback_query(ChatOwnerBroadcastStates.enter_button, F.data.startswith("mychats:custombc:buttons:done:"))
async def cb_custom_broadcast_buttons_done(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _finalize_broadcast_text(callback, session, state)


@router.callback_query(F.data.startswith("mychats:custombc:interval:"))
async def cb_custom_broadcast_interval_start(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext,
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return

    min_interval = await SettingsRepository(session).get_int("broadcast_min_interval_seconds", 300)
    await state.set_state(ChatOwnerBroadcastStates.enter_interval)
    await state.update_data(chat_id=chat_id)
    text = f"⏱ Введи интервал в секундах (не меньше {min_interval}):"
    try:
        await callback.message.edit_text(text, reply_markup=custom_broadcast_cancel_kb(chat_id))
    except Exception:
        await callback.message.answer(text, reply_markup=custom_broadcast_cancel_kb(chat_id))
    await callback.answer()


@router.message(ChatOwnerBroadcastStates.enter_interval)
async def msg_custom_broadcast_interval(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("chat_id")
    chat = await ChatRepository(session).get(chat_id) if chat_id else None
    if chat is None or chat.owner_user_id != message.from_user.id:
        await state.clear()
        return

    min_interval = await SettingsRepository(session).get_int("broadcast_min_interval_seconds", 300)
    try:
        seconds = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Введи целое число секунд:", reply_markup=custom_broadcast_cancel_kb(chat_id))
        return
    if seconds < min_interval:
        await message.answer(
            f"❌ Интервал не может быть меньше {min_interval} секунд:",
            reply_markup=custom_broadcast_cancel_kb(chat_id),
        )
        return

    chat.custom_broadcast_interval_seconds = seconds
    await session.commit()
    await state.clear()
    await message.answer(f"✅ Интервал установлен: {seconds} сек.")
    await _send_broadcast_panel(message, session, chat)


@router.callback_query(F.data.startswith("mychats:custombc:toggle:"))
async def cb_custom_broadcast_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return

    if not chat.custom_broadcast_enabled:
        approved = await ChatBroadcastRepository(session).count_approved(chat_id)
        if approved == 0:
            await callback.answer(
                "❌ Нужен хотя бы один одобренный модератором текст.", show_alert=True,
            )
            return
        if not chat.custom_broadcast_interval_seconds:
            await callback.answer("❌ Сначала задай интервал.", show_alert=True)
            return

    chat.custom_broadcast_enabled = not chat.custom_broadcast_enabled
    await session.commit()
    await _render_broadcast_panel(callback, session, chat)
    await callback.answer("✅ Включено" if chat.custom_broadcast_enabled else "✅ Выключено")


@router.callback_query(F.data.startswith("mychats:custombc:"))
async def cb_custom_broadcast_panel(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return
    await state.clear()
    await _render_broadcast_panel(callback, session, chat)
    await callback.answer()


# --- FSM-continuation steps re-registered here so the exact same handlers
# also fire when the owner is typing/tapping from their PRIVATE chat
# instead of the group. These are the SAME functions the group flow uses
# — every one of them only ever reads its target chat_id from FSM state
# data (never from the triggering message's own chat), and re-verifies
# ownership itself, so nothing is duplicated, just re-wired to fire from
# a second (private-chat-only) router too.
router.message.register(msg_chat_promo_code, ChatOwnerPromoStates.enter_code)
router.message.register(msg_bonus_code, ChatOwnerBonusStates.enter_code)
router.message.register(msg_bonus_reward, ChatOwnerBonusStates.enter_reward)
router.message.register(msg_bonus_limit, ChatOwnerBonusStates.enter_limit)
router.callback_query.register(cb_bonus_mode, ChatOwnerBonusStates.choose_mode, F.data.startswith("chatbonus:mode:"))
router.callback_query.register(
    cb_bonus_add_sponsor_start, ChatOwnerBonusStates.choose_sponsors, F.data.startswith("chatbonus:addsponsor:"),
)
router.callback_query.register(
    cb_bonus_sponsors_done, ChatOwnerBonusStates.choose_sponsors, F.data == "chatbonus:sponsors:done",
)
