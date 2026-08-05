import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import Chat
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_promo import ChatPromoRepository
from bot.database.repositories.game import GameRepository
from bot.database.repositories.settings import SettingsRepository
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
from bot.keyboards.mychats import mychat_back_kb, mychat_panel_kb, mychats_list_kb
from bot.states.group import ChatOwnerBonusStates, ChatOwnerPromoStates

router = Router()
logger = logging.getLogger(__name__)
settings = get_settings()

_GAME_LABELS = {"roulette": "Рулетка", "doors": "Двери", "maze": "Лабиринт", "tower": "Башня"}


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
    await state.clear()
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    chat = await _verify_owned_chat(callback, session, chat_id)
    if chat is None:
        return
    await _render_panel(callback, session, chat)
    await callback.answer()


@router.callback_query(F.data.startswith("mychats:refresh:"))
async def cb_mychats_refresh(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
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
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    if await _verify_owned_chat(callback, session, chat_id) is None:
        return
    await start_promo_creation(callback, session, state, chat_id)


@router.callback_query(F.data.startswith("mychats:bonus:"))
async def cb_mychats_bonus_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    chat_id = _parse_chat_id(callback)
    if chat_id is None:
        await callback.answer()
        return
    if await _verify_owned_chat(callback, session, chat_id) is None:
        return
    await start_bonus_creation(callback, state, chat_id)


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
