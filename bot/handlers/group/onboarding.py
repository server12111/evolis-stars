import logging

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.settings import SettingsRepository

router = Router()
logger = logging.getLogger(__name__)

_LEFT_STATUSES = {"left", "kicked"}
_PRESENT_STATUSES = {"member", "administrator", "restricted"}


async def _resolve_owner(bot: Bot, chat_id: int) -> int | None:
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except Exception as exc:
        logger.warning("Cannot resolve owner for chat %s: %s", chat_id, exc)
        return None
    for admin in admins:
        if admin.status == "creator":
            return admin.user.id
    return None


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot, session: AsyncSession) -> None:
    """Fires when the bot itself is added to / removed from / promoted in a
    chat. This is how a Chat row first gets created — no group.message
    handler can run before the bot has actually been added."""
    new_status = event.new_chat_member.status
    chat_id = event.chat.id

    if new_status in _LEFT_STATUSES:
        await ChatRepository(session).mark_left(chat_id)
        return

    if new_status not in _PRESENT_STATUSES:
        return

    try:
        member_count = await bot.get_chat_member_count(chat_id)
    except Exception as exc:
        logger.warning("Cannot get member count for chat %s: %s", chat_id, exc)
        member_count = 0

    owner_user_id = await _resolve_owner(bot, chat_id)
    min_members = await SettingsRepository(session).get_int("chat_min_members", 250)

    await ChatRepository(session).upsert(
        chat_id=chat_id,
        title=event.chat.title or "",
        member_count=member_count,
        owner_user_id=owner_user_id,
        min_members=min_members,
    )
