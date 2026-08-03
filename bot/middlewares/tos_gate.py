from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.keyboards.tos import tos_accept_kb
from bot.services.tos import get_tos_urls

settings = get_settings()

_BYPASS_PREFIXES = ("/start", "/admin", "admin:", "tos_accept", "sponsor_check", "wall_check")


def _should_skip(callback_data: str | None, message_text: str | None) -> bool:
    if message_text and any(message_text.startswith(prefix) for prefix in _BYPASS_PREFIXES):
        return True
    if callback_data and any(callback_data.startswith(prefix) for prefix in _BYPASS_PREFIXES):
        return True
    return False


class TosGateMiddleware(BaseMiddleware):
    """Runs BEFORE SponsorWallMiddleware — ToS/privacy-policy acceptance is
    now the very first gate a user must clear, ahead of the sponsor wall.
    Until "Принимаю" is tapped, every other private-chat update is
    intercepted and replaced with the ToS screen. Group-chat updates are
    never gated (group members aren't onboarded via /start the same way,
    and the group feature must keep working)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        db_user: User | None = data.get("db_user")
        if not db_user:
            return await handler(event, data)

        if db_user.is_admin or db_user.user_id in settings.admin_id_list:
            return await handler(event, data)

        if db_user.tos_accepted:
            return await handler(event, data)

        inner: Message | CallbackQuery | None = None
        if isinstance(event, Update):
            inner = event.message or event.callback_query
        elif isinstance(event, (Message, CallbackQuery)):
            inner = event
        if inner is None:
            return await handler(event, data)

        chat = inner.chat if isinstance(inner, Message) else (inner.message.chat if inner.message else None)
        if chat is not None and chat.type != "private":
            return await handler(event, data)

        callback_data = inner.data if isinstance(inner, CallbackQuery) else None
        message_text = inner.text if isinstance(inner, Message) else None
        if _should_skip(callback_data, message_text):
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        if session is None:
            return await handler(event, data)

        text = await ContentRepository(session).get_text("tos")
        user_agreement_url, privacy_policy_url = await get_tos_urls(session)
        kb = tos_accept_kb(user_agreement_url, privacy_policy_url)
        if isinstance(inner, Message):
            await inner.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        elif inner.message:
            await inner.message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            await inner.answer()
        return
