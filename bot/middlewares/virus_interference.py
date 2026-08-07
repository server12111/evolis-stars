from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.repositories.virus import VirusInfectionRepository
from bot.services.virus_game import INTERFERENCE_TEXT, roll_interference

settings = get_settings()


class VirusInterferenceMiddleware(BaseMiddleware):
    """A "Вирус"-infected user has a global, per-ТЗ-without-exceptions 5%
    chance of any command (including "Антивирус" itself) being blocked
    with INTERFERENCE_TEXT instead of reaching its handler -- applies in
    every chat type, since the infection status itself is account-wide."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        if session is None:
            return await handler(event, data)

        inner: Message | CallbackQuery | None = None
        if isinstance(event, Update):
            inner = event.message or event.callback_query
        elif isinstance(event, (Message, CallbackQuery)):
            inner = event
        if inner is None:
            return await handler(event, data)

        # A real Telegram Stars payment confirmation must always reach its
        # handler -- same money-safety carve-out SponsorWallMiddleware
        # already makes, orthogonal to the "no exceptions" rule for the
        # game's own commands (that rule is about "Антивирус", not payments).
        if isinstance(inner, Message) and inner.successful_payment is not None:
            return await handler(event, data)

        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.id in settings.admin_id_list:
            return await handler(event, data)

        infection = await VirusInfectionRepository(session).get(tg_user.id)
        if infection is None:
            return await handler(event, data)

        if not roll_interference():
            return await handler(event, data)

        if isinstance(inner, Message):
            try:
                await inner.reply(INTERFERENCE_TEXT)
            except Exception:
                pass
        else:
            try:
                await inner.answer(INTERFERENCE_TEXT, show_alert=True)
            except Exception:
                pass
        return None