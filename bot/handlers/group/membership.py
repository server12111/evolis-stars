from aiogram import Router
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.chat_membership import ChatMembershipRepository

router = Router()

_LEFT_STATUSES = {"left", "kicked"}


@router.chat_member()
async def on_chat_member_update(event: ChatMemberUpdated, session: AsyncSession) -> None:
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
