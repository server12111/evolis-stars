import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_promo import ChatPromoRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.group.owner_menu import owner_menu_kb

router = Router()
logger = logging.getLogger(__name__)


async def _render_menu_text(chat_repo: ChatRepository, chat_id: int, member_count: int) -> str:
    count, total_balance = await chat_repo.registered_members_summary(chat_id)
    return (
        f"⚙️ <b>Управление чатом</b>\n\n"
        f"🆔 ID чата: <code>{chat_id}</code>\n"
        f"👥 Участников: <b>{member_count}</b>\n"
        f"📋 Зарегистрировано в Evolis: <b>{count}</b>\n"
        f"💰 Баланс всех зарегистрированных участников: <b>{total_balance:.2f} ⭐</b>"
    )


@router.message(Command("EvolisOpen", ignore_case=True))
async def cmd_evolis_open(message: Message, bot: Bot, session: AsyncSession) -> None:
    chat_id = message.chat.id
    if message.from_user is None:
        return

    settings_repo = SettingsRepository(session)
    min_members = await settings_repo.get_int("chat_min_members", 250)

    try:
        member_count = await bot.get_chat_member_count(chat_id)
    except Exception as exc:
        logger.warning("Cannot get member count for chat %s: %s", chat_id, exc)
        await message.reply("⚠️ Не удалось проверить количество участников. Попробуйте позже.")
        return

    if member_count < min_members:
        await message.reply(
            f"⚠️ Управление чатом доступно от {min_members} участников. "
            f"Сейчас в чате: {member_count}."
        )
        return

    try:
        member = await bot.get_chat_member(chat_id, message.from_user.id)
    except Exception as exc:
        logger.warning("Cannot verify chat owner for %s: %s", chat_id, exc)
        return
    if member.status != "creator":
        # Only the actual chat creator ever sees anything from this command.
        return

    chat_repo = ChatRepository(session)
    chat = await chat_repo.upsert(
        chat_id=chat_id,
        title=message.chat.title or "",
        member_count=member_count,
        owner_user_id=message.from_user.id,
        min_members=min_members,
    )

    has_promo = await ChatPromoRepository(session).get_by_chat(chat_id) is not None
    text = await _render_menu_text(chat_repo, chat_id, member_count)
    await message.answer(text, parse_mode="HTML", reply_markup=owner_menu_kb(chat.broadcast_opt_in, has_promo))


@router.callback_query(F.data == "chatmenu:refresh")
async def cb_chat_menu_refresh(callback: CallbackQuery, bot: Bot, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get(chat_id)
    if not chat or callback.from_user.id != chat.owner_user_id:
        await callback.answer()
        return

    try:
        member_count = await bot.get_chat_member_count(chat_id)
    except Exception:
        member_count = chat.member_count

    min_members = await SettingsRepository(session).get_int("chat_min_members", 250)
    chat = await chat_repo.upsert(
        chat_id=chat_id,
        title=callback.message.chat.title or chat.title,
        member_count=member_count,
        owner_user_id=chat.owner_user_id,
        min_members=min_members,
    )

    has_promo = await ChatPromoRepository(session).get_by_chat(chat_id) is not None
    text = await _render_menu_text(chat_repo, chat_id, member_count)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=owner_menu_kb(chat.broadcast_opt_in, has_promo))
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer("✅ Обновлено")


@router.callback_query(F.data == "chatmenu:broadcast_toggle")
async def cb_chat_broadcast_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get(chat_id)
    if not chat or callback.from_user.id != chat.owner_user_id:
        await callback.answer()
        return

    chat.broadcast_opt_in = not chat.broadcast_opt_in
    await chat_repo.session.commit()

    text = await _render_menu_text(chat_repo, chat_id, chat.member_count)
    if chat.broadcast_opt_in:
        text += (
            "\n\n📣 Реклама включена: боту разрешено периодически присылать участникам "
            "рекламу от BotoHub в личные сообщения и публиковать рекламные посты с кнопками в чате.\n"
            "За каждую 1000 показов вам начисляется 0.5 ⭐, а после 400 переходов по кнопкам — "
            "ещё разово 4 ⭐ на баланс."
        )
    has_promo = await ChatPromoRepository(session).get_by_chat(chat_id) is not None
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=owner_menu_kb(chat.broadcast_opt_in, has_promo))
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer("✅ Включено" if chat.broadcast_opt_in else "✅ Выключено")
