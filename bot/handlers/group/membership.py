import logging

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.repositories.chat_membership import ChatMembershipRepository

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)

_LEFT_STATUSES = {"left", "kicked"}

_WELCOME_TEXT = (
    "👋 Добро пожаловать в чат!\n\n"
    "Напиши «инфо», чтобы подробно ознакомиться со всеми командами бота.\n\n"
    "⭐️ <b>EvolisStars</b> — зарабатывай RP⭐️ за рефералов, игры и бонусы, "
    "выводи их в настоящие Telegram Stars себе или в подарок другу!"
)


def _welcome_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Перейти в бота", url=f"https://t.me/{settings.bot_username}"),
    ]])


def _just_joined(event: ChatMemberUpdated) -> bool:
    """True only for a genuine transition into the chat (old status was
    left/kicked, new status is an active membership) — not for promotions,
    demotions, or any other status change of someone already present."""
    return (
        event.old_chat_member.status in _LEFT_STATUSES
        and event.new_chat_member.status not in _LEFT_STATUSES
    )


async def _send_welcome(bot: Bot, chat_id: int, user_id: int) -> None:
    """Sent as an Ephemeral Message (Bot API 10.2, receiver_user_id) — visible
    only to the newly-joined member, not the whole chat."""
    try:
        await bot.send_message(
            chat_id,
            _WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=_welcome_kb(),
            receiver_user_id=user_id,
        )
    except Exception:
        logger.warning("Could not send welcome message to user %s in chat %s", user_id, chat_id, exc_info=True)


@router.chat_member()
async def on_chat_member_update(event: ChatMemberUpdated, session: AsyncSession, bot: Bot) -> None:
    """Keeps ChatMembership in sync with real join/leave/promote events for
    chat members OTHER than the bot itself (that's on_my_chat_member)."""
    chat_id = event.chat.id
    user_id = event.new_chat_member.user.id
    status = event.new_chat_member.status

    repo = ChatMembershipRepository(session)
    if status in _LEFT_STATUSES:
        await repo.mark_left(chat_id, user_id)
    else:
        await repo.mark_joined(chat_id, user_id)

    if _just_joined(event) and not event.new_chat_member.user.is_bot:
        await _send_welcome(bot, chat_id, user_id)
