import asyncio
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.repositories.user import UserRepository

settings = get_settings()

# Aiogram can process updates concurrently. A small lock pool prevents two
# rapid clicks from spending/refunding the same user's balance at once while
# keeping unrelated users concurrent and avoiding an unbounded lock dict.
_USER_LOCKS = [asyncio.Lock() for _ in range(256)]


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession = data.get("session")
        if not session:
            return await handler(event, data)

        tg_user = data.get("event_from_user")
        if not tg_user:
            return await handler(event, data)

        lock = _USER_LOCKS[tg_user.id % len(_USER_LOCKS)]
        async with lock:
            repo = UserRepository(session)
            user, is_new, previous_last_seen_at = await repo.get_or_create(
                user_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name or "",
            )

            if user.is_blocked and tg_user.id not in settings.admin_id_list:
                inner = data.get("event_context") or (
                    event.message if hasattr(event, "message") else None
                ) or (event.callback_query if hasattr(event, "callback_query") else None)
                if inner and hasattr(inner, "answer"):
                    await inner.answer("❌ Вы заблокированы в боте.")
                return

            should_be_admin = tg_user.id in settings.admin_id_list
            if user.is_admin != should_be_admin:
                user.is_admin = should_be_admin
                await session.commit()

            data["db_user"] = user
            data["is_new_user"] = is_new
            data["previous_last_seen_at"] = previous_last_seen_at
            return await handler(event, data)
