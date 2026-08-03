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
    old_status = event.old_chat_member.status
    chat_id = event.chat.id

    if new_status in _LEFT_STATUSES:
        await ChatRepository(session).mark_left(chat_id)
        return

    if new_status not in _PRESENT_STATUSES:
        return

    owner_user_id = await _resolve_owner(bot, chat_id)

    # Only the actual creator is allowed to invite the bot in the first
    # place — check this only on the genuine "just added" transition, not
    # on a later promotion (e.g. the creator making the bot an admin).
    just_added = old_status not in _PRESENT_STATUSES
    if just_added and owner_user_id is not None:
        adder = getattr(event, "from_user", None)
        adder_id = adder.id if adder else None
        # Only block when we could actually identify who added the bot —
        # missing from_user data must fail open, not falsely kick the bot.
        if adder_id is not None and adder_id != owner_user_id:
            logger.info("Bot added to chat %s by non-owner %s — leaving.", chat_id, adder_id)
            try:
                await bot.send_message(
                    chat_id,
                    "⚠️ Добавлять бота в чат может только владелец (создатель) чата. Бот покидает чат.",
                )
            except Exception:
                pass
            try:
                await bot.leave_chat(chat_id)
            except Exception as exc:
                logger.warning("Failed to leave chat %s after non-owner add: %s", chat_id, exc)
            return

    try:
        member_count = await bot.get_chat_member_count(chat_id)
    except Exception as exc:
        logger.warning("Cannot get member count for chat %s: %s", chat_id, exc)
        member_count = 0

    min_members = await SettingsRepository(session).get_int("chat_min_members", 250)

    await ChatRepository(session).upsert(
        chat_id=chat_id,
        title=event.chat.title or "",
        member_count=member_count,
        owner_user_id=owner_user_id,
        min_members=min_members,
    )
