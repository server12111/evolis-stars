from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User

router = Router()

_BALANCE_WORDS = {"б", "бал", "балик", "баланс"}


def _matches_balance(message: Message) -> bool:
    return bool(message.text and message.text.strip().lower() in _BALANCE_WORDS)


@router.message(_matches_balance)
async def msg_balance(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    user = await session.get(User, message.from_user.id)
    if user is None:
        await message.reply("❌ Нужно быть зарегистрированным в боте — напиши /start в личных сообщениях.")
        return
    await message.reply(f"💰 Твой баланс: <b>{float(user.stars_balance):.2f} ⭐</b>", parse_mode="HTML")
