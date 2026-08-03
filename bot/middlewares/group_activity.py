from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from bot.database.repositories.chat_membership import ChatMembershipRepository


class GroupActivityMiddleware(BaseMiddleware):
    """Tracks per-chat message counts independently of which (if any) group
    handler ultimately matches a message — this is the sole place
    ChatMembership.message_count gets incremented."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        event_chat = data.get("event_chat")
        tg_user = data.get("event_from_user")

        if (
            session is not None
            and event_chat is not None
            and event_chat.type in ("group", "supergroup")
            and tg_user is not None
            and isinstance(event, Update)
            and event.message is not None
        ):
            await ChatMembershipRepository(session).touch_message(event_chat.id, tg_user.id)

        return await handler(event, data)
