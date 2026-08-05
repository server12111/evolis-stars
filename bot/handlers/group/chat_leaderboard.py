from html import escape

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.chat import ChatRepository

router = Router()


def _chat_link(chat) -> str:
    """Public chats link via @username, private chats via a previously
    saved invite link (never freshly generated), otherwise plain text —
    same priority as bot/handlers/admin/chats.py's _chat_link."""
    title = escape(chat.title or str(chat.chat_id)) or "Без названия"
    if chat.username:
        return f'<a href="https://t.me/{chat.username}">{title}</a>'
    if chat.invite_link:
        return f'<a href="{escape(chat.invite_link)}">{title}</a>'
    return title


@router.message(lambda m: (m.text or "").strip().lower() == "топ чатов")
async def msg_chat_leaderboard(message: Message, session: AsyncSession) -> None:
    chats = await ChatRepository(session).top_by_member_count(limit=10)
    if not chats:
        await message.reply("Пока нет ни одного зарегистрированного чата.")
        return

    lines = [
        f"{i}. {_chat_link(chat)} — <b>{chat.member_count}</b> участников"
        for i, chat in enumerate(chats, start=1)
    ]
    text = "🏆 <b>Топ 10 чатов по числу участников</b>\n\n" + "\n".join(lines)
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
