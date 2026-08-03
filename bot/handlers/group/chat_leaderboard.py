from html import escape

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.chat import ChatRepository

router = Router()


@router.message(lambda m: (m.text or "").strip().lower() == "топ чатов")
async def msg_chat_leaderboard(message: Message, session: AsyncSession) -> None:
    chats = await ChatRepository(session).top_by_member_count(limit=10)
    if not chats:
        await message.reply("Пока нет ни одного зарегистрированного чата.")
        return

    lines = [
        f"{i}. {escape(chat.title) or 'Без названия'} — <b>{chat.member_count}</b> участников"
        for i, chat in enumerate(chats, start=1)
    ]
    text = "🏆 <b>Топ 10 чатов по числу участников</b>\n\n" + "\n".join(lines)
    await message.answer(text, parse_mode="HTML")
