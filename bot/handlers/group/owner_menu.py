import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.repositories.chat import ChatRepository

router = Router()
logger = logging.getLogger(__name__)
settings = get_settings()


async def render_chat_panel_text(chat_repo: ChatRepository, chat_id: int, member_count: int) -> str:
    count, total_balance = await chat_repo.registered_members_summary(chat_id)
    return (
        f"⚙️ <b>Управление чатом</b>\n\n"
        f"🆔 ID чата: <code>{chat_id}</code>\n"
        f"👥 Участников: <b>{member_count}</b>\n"
        f"📋 Зарегистрировано в Evolis: <b>{count}</b>\n"
        f"💰 Баланс всех зарегистрированных участников: <b>{total_balance:.2f} ⭐</b>"
    )


@router.message(Command("EvolisOpen", ignore_case=True))
async def cmd_evolis_open(message: Message, session: AsyncSession) -> None:
    """Management moved entirely to the private chat panel — this command
    is now just a pointer there, not a functional panel. Existing chat
    data/settings are untouched; only this in-group entry point changed."""
    chat_id = message.chat.id
    if message.from_user is None:
        return
    chat = await ChatRepository(session).get(chat_id)
    if not chat or chat.owner_user_id != message.from_user.id:
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="💬 Открыть в личном чате",
        url=f"https://t.me/{settings.bot_username}?start=mychats",
    ))
    await message.reply(
        "⚙️ Управление чатом теперь доступно в личном чате с ботом — "
        "раздел «💬 Панель чатов» в главном меню.",
        reply_markup=builder.as_markup(),
    )
