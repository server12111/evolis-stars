import logging
import re
from html import escape, unescape
from urllib.parse import urlparse

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
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
from bot.database.repositories.chat_sponsor_wall import ChatSponsorWallRepository
from bot.database.repositories.game import GameRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository
from bot.handlers.group.chat_bonus import (
    cb_bonus_add_sponsor_reuse,
    cb_bonus_add_sponsor_start,
    cb_bonus_mode,
    cb_bonus_sponsors_done,
    msg_bonus_code,
    msg_bonus_limit,
    msg_bonus_reward,
    start_bonus_creation,
)
from bot.handlers.group.chat_promo import msg_chat_promo_code, start_promo_creation
from bot.handlers.group.chat_sponsor_wall import (
    MAX_WALL_SPONSORS_HARD_CAP,
    cb_wall_sponsor_addnew,
    cb_wall_sponsor_reuse,
    start_wall_sponsor_flow,
)
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
    mychat_wall_kb,
    mychats_list_kb,
)
from bot.services.content_moderation import find_banned_term
from bot.states.group import (
    ChatOwnerBonusStates,
    ChatOwnerBroadcastStates,
    ChatOwnerPromoStates,
    ChatOwnerWallStates,
)

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
    kb = mychat_panel_kb(chat.chat_id, chat.broadcast_opt_in, has_promo, chat.games_enabled)
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


@router.callback_query(F.data.startswith("mychats:gamestoggle:"))
async def cb_mychats_gamestoggle(callback: CallbackQuery, session: AsyncSession) -> None:
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

    chat.games_enabled = not chat.games_enabled
    await session.commit()
    await _render_panel(callback, session, chat)
    await callback.answer("✅ Игры включены" if chat.games_enabled else "✅ Игры выключены")


async def _render_wall(callback: CallbackQuery, session: AsyncSession, chat: Chat) -> None:
    sponsors = await ChatSponsorWallRepository(session).list_all(chat.chat_id)
    active_count = sum(1 for s in sponsors if s.is_active)
    text = (
        f"🚧 <b>Спонсор-стена</b>\n\n"
        f"Пока участник не подпишется на всех активных спонсорах ниже, бот удаляет его сообщения "
        f"в этом чате. За каждого спонсора участник получает <b>1 RP⭐️</b>.\n\n"
        f"Активно: <b>{active_count}/{chat.sponsor_wall_max_sponsors}</b>"
    )
    kb = mychat_wall_kb(chat.chat_id, sponsors, chat.sponsor_wall_max_sponsors)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        if "message is not modified" in str(exc).lower():
            return
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("mychats:wall:"))
async def cb_mychats_wall(callback: CallbackQuery, session: AsyncSession) -> None:
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
    await _render_wall(callback, session, chat)
    await callback.answer()


@router.callback_query(F.data.startswith("mychats:walladd:"))
async def cb_mychats_walladd(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot,
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        chat_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    sponsor_type = parts[3]
    if sponsor_type not in ("channel", "chat"):
        await callback.answer()
        return
    await start_wall_sponsor_flow(callback, state, session, bot, chat_id, sponsor_type)


@router.callback_query(F.data.startswith("mychats:walltoggle:"))
async def cb_mychats_walltoggle(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        chat_id, sponsor_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return
    sponsor = await ChatSponsorWallRepository(session).toggle(sponsor_id, chat_id)
    if sponsor is None:
        await callback.answer("❌ Спонсор не найден.", show_alert=True)
        return
    await _render_wall(callback, session, chat)
    await callback.answer("✅ Включен" if sponsor.is_active else "✅ Выключен")


@router.callback_query(F.data.startswith("mychats:walldelete:"))
async def cb_mychats_walldelete(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        chat_id, sponsor_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return
    ok = await ChatSponsorWallRepository(session).delete(sponsor_id, chat_id)
    if not ok:
        await callback.answer("❌ Спонсор не найден.", show_alert=True)
        return
    await _render_wall(callback, session, chat)
    await callback.answer("🗑 Удалён")


@router.callback_query(F.data.startswith("mychats:walllimit:"))
async def cb_mychats_walllimit(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
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
    await state.set_state(ChatOwnerWallStates.enter_limit)
    await state.update_data(chat_id=chat_id)
    await callback.message.answer(
        f"✏️ Введите новый лимит спонсоров на стене (1-{MAX_WALL_SPONSORS_HARD_CAP}):",
        reply_markup=mychat_back_kb(chat_id),
    )
    await callback.answer()


@router.message(ChatOwnerWallStates.enter_limit)
async def msg_wall_limit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    chat = await _verify_owned_chat_for_state(session, data, message.from_user.id)
    if chat is None:
        await state.clear()
        return
    chat_id = chat.chat_id
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("❌ Введите целое число:", reply_markup=mychat_back_kb(chat_id))
        return
    value = int(raw)
    if value < 1 or value > MAX_WALL_SPONSORS_HARD_CAP:
        await message.answer(
            f"❌ Лимит должен быть от 1 до {MAX_WALL_SPONSORS_HARD_CAP}:",
            reply_markup=mychat_back_kb(chat_id),
        )
        return
    chat.sponsor_wall_max_sponsors = value
    await session.commit()
    await state.clear()
    await message.answer(f"✅ Лимит спонсоров на стене: {value}", reply_markup=mychat_back_kb(chat_id))


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


_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_plain(html_text: str) -> str:
    """Strips tags and decodes entities from an html_text string, back to
    the plain characters a human actually reads. Used both for a safe
    preview and for running the banned-word filter -- an html_text value
    is NOT what it should be checked against directly, since a single
    bold/italic/etc. entity placed on one letter of a banned word (trivial
    to do from a stock Telegram client -- select a letter, tap Bold)
    inserts a tag in the middle of it, e.g. "порно" -> "пор<b>н</b>о",
    which splits it into fragments that never match the word-based filter."""
    return unescape(_TAG_RE.sub("", html_text))


def _plain_text_preview(message: ChatBroadcastMessage, limit: int) -> str:
    """Short, HTML-safe preview of a stored broadcast text for the owner's
    own panel (which is itself sent with parse_mode="HTML"). Stripping
    tags and decoding entities BEFORE truncating avoids slicing a valid
    <tg-emoji ...> tag in half at the character limit (which would leave a
    dangling "<tg-emo" fragment and break the whole panel message's HTML
    parse); the plain result is then escaped fresh so it can never smuggle
    real markup into a message it wasn't meant to format."""
    plain = _html_to_plain(message.text) if message.text_is_html else message.text
    truncated = plain if len(plain) <= limit else plain[: limit - 3] + "..."
    return escape(truncated)


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
            preview = _plain_text_preview(message, 60)
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

    # html_text serializes any real Telegram formatting/premium-emoji
    # entities into valid <tag>s and escapes literal "<"/">"/"&" in plain
    # portions, so it's safe to send later with parse_mode="HTML" (see
    # ChatBroadcastMessage.text_is_html) for everything EXCEPT one entity
    # kind aiogram doesn't escape into: a text_link's own url is spliced
    # straight into href="..." unescaped, so a link containing '"' or a
    # bare '<'/'>' would break out of the attribute — reject those before
    # they ever get this far. Lets an owner include actual premium emoji
    # by inserting them normally in Telegram, not typing raw markup.
    if any(
        entity.type == "text_link" and entity.url and any(ch in entity.url for ch in ('"', "<", ">"))
        for entity in (message.entities or [])
    ):
        await message.answer(
            "❌ Ссылка в тексте содержит недопустимые символы. Отправь другой текст:",
            reply_markup=custom_broadcast_cancel_kb(chat_id),
        )
        return
    body = (message.html_text or "").strip()
    if not body:
        await message.answer("❌ Отправь текстовое сообщение:", reply_markup=custom_broadcast_cancel_kb(chat_id))
        return
    if len(body) > _BROADCAST_MAX_TEXT_LEN:
        await message.answer(
            f"❌ Слишком длинно (максимум {_BROADCAST_MAX_TEXT_LEN} символов):",
            reply_markup=custom_broadcast_cancel_kb(chat_id),
        )
        return
    # Checked against the PLAIN reading of the text, not the html_text with
    # its <tag>s still in -- formatting entities split words apart in
    # html_text (see _html_to_plain's own docstring) and would otherwise
    # let a single Bold tap on one letter evade the filter entirely.
    if find_banned_term(_html_to_plain(body)):
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
        extra_lines.append("📸 Фото приложены выше.")
    if buttons:
        extra_lines.append("🔘 Кнопки:")
        extra_lines.extend(f"  • {escape(b['text'])} → {escape(b['url'])}" for b in buttons)
    extras = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    header = (
        f"📋 <b>Новый текст рассылки на модерацию</b>\n\n"
        f"💬 Чат: {escape(chat.title or str(chat.chat_id))} (<code>{chat.chat_id}</code>)\n"
        f"👤 Владелец: {owner_display} (<code>{chat.owner_user_id}</code>)\n\n"
        f"Текст:\n<code>"
    )
    footer = f"</code>{extras}"
    escaped_body = escape(broadcast_msg.text)
    # html.escape() can expand a character up to 6x ('"' -> '&quot;'), so a
    # text near the (already-capped) 1000-char limit that's heavy on
    # quotes/&/<>/ can still blow past Telegram's 4096-char message cap —
    # send_message would then raise, the row would stay "pending" forever
    # with nothing posted for a moderator to see, and the owner has no way
    # to resubmit since the 1-text-per-chat slot stays occupied. Truncate
    # conservatively (raw chars, not the already-escaped string, so an
    # entity like "&amp;" is never cut in half) to guarantee it fits.
    budget = 4096 - len(header) - len(footer) - 60
    if len(escaped_body) > budget:
        notice = "\n\n[текст обрезан для показа здесь — полный текст в панели чата]"
        raw_budget = max(0, (budget - len(notice)) // 6)
        escaped_body = escape(broadcast_msg.text[:raw_budget]) + notice
    text = header + escaped_body + footer
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"broadcast_mod:approve:{broadcast_msg.id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"broadcast_mod:reject:{broadcast_msg.id}"),
    ]])

    # Photos (if any) are sent as their own message(s) first, with no
    # caption/buttons — a moderator reviewing image-based violations the
    # keyword filter can't catch needs to actually SEE them, not just read
    # a "📸 Фото: 3" count. The decision buttons always end up on the
    # separate text message below regardless of photo count, so
    # approve/reject's edit_text-on-decision never has to special-case a
    # photo caption (sendMediaGroup can't carry buttons at all anyway).
    if len(photos) == 1:
        try:
            await bot.send_photo(int(channel_id), photos[0])
        except Exception as exc:
            logger.warning("Cannot attach photo for moderation post %s: %s", broadcast_msg.id, exc)
    elif len(photos) > 1:
        try:
            await bot.send_media_group(int(channel_id), [InputMediaPhoto(media=fid) for fid in photos])
        except Exception as exc:
            logger.warning("Cannot attach photos for moderation post %s: %s", broadcast_msg.id, exc)

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
        chat.chat_id, body, photos or None, buttons or None, text_is_html=True,
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
    # Telegram rejects the button (and the WHOLE send) at delivery time for
    # a host with no real TLD -- e.g. a chat owner typing "https://MyBot"
    # meaning a link to the bot, instead of "https://t.me/MyBot". That
    # failure only ever surfaces later as a silent per-broadcast-attempt
    # warning in the scheduler, so catch it here instead.
    parsed_url = urlparse(url) if url.startswith(("http://", "https://")) else None
    if not label or not parsed_url or not parsed_url.netloc or "." not in parsed_url.netloc:
        await message.answer(
            "❌ Неверный формат. Отправь так:\nТекст кнопки - https://ссылка\n\n"
            "Ссылка должна вести на настоящий сайт или t.me (например, https://t.me/EvolisStarsBot, а не https://EvolisStarsBot):",
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
    cb_bonus_add_sponsor_reuse, ChatOwnerBonusStates.choose_sponsors, F.data.startswith("chatbonus:reuse:"),
)
router.callback_query.register(
    cb_bonus_sponsors_done, ChatOwnerBonusStates.choose_sponsors, F.data == "chatbonus:sponsors:done",
)
# The sponsor-wall screen only ever lives in this private chat panel (never
# the group owner-menu), unlike the bonus flow above -- no FSM state filter
# needed, ownership is re-checked inside the handlers themselves.
router.callback_query.register(cb_wall_sponsor_addnew, F.data.startswith("chatwall:addnew:"))
router.callback_query.register(cb_wall_sponsor_reuse, F.data.startswith("chatwall:reuse:"))
