import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository
from bot.services.country_notice import ensure_country_notice
from bot.services.sponsor_results import all_configured_integrations_failed
from bot.services.sponsor_waves import (
    evaluate_waves,
    sponsor_wave_markup,
    sponsor_wave_text,
)

settings = get_settings()
logger = logging.getLogger(__name__)

_BYPASS_PREFIXES = (
    "/start",
    "/admin",
    "admin:",
    "wall_check",
    "sponsor_check",
    "captcha:",
)


def _should_skip(callback_data: str | None, message_text: str | None) -> bool:
    if message_text and any(
        message_text.startswith(prefix) for prefix in _BYPASS_PREFIXES
    ):
        return True
    if callback_data and any(
        callback_data.startswith(prefix) for prefix in _BYPASS_PREFIXES
    ):
        return True
    return False


async def _show_wave(
    inner: Message | CallbackQuery,
    *,
    wave: int,
    total_waves: int,
    items: list[dict],
) -> None:
    text = sponsor_wave_text(wave, total_waves)
    markup = sponsor_wave_markup(items)
    if isinstance(inner, Message):
        await inner.answer(text, parse_mode="HTML", reply_markup=markup)
        return

    if not inner.message:
        await inner.answer()
        return
    try:
        await inner.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception:
        await inner.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    await inner.answer()


async def _show_retry(inner: Message | CallbackQuery) -> None:
    text = (
        "⚠️ Не удалось проверить обязательных спонсоров.\n\n"
        "Попробуйте нажать кнопку проверки ещё раз через несколько секунд."
    )
    if isinstance(inner, Message):
        await inner.answer(text)
    elif inner.message:
        await inner.message.answer(text)
        await inner.answer()


class SponsorWallMiddleware(BaseMiddleware):
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

        inner: Message | CallbackQuery | None = None
        if isinstance(event, Update):
            inner = event.message or event.callback_query
        elif isinstance(event, (Message, CallbackQuery)):
            inner = event
        if inner is None:
            return await handler(event, data)

        callback_data = inner.data if isinstance(inner, CallbackQuery) else None
        message_text = inner.text if isinstance(inner, Message) else None
        if _should_skip(callback_data, message_text):
            return await handler(event, data)

        # The contact must reach the FSM phone handler.
        if isinstance(inner, Message) and inner.contact:
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        state = data.get("state")
        bot: Bot | None = data.get("bot")
        if session is None or state is None:
            return await handler(event, data)

        if db_user.sponsors_verified:
            if await _phone_required(session, db_user):
                await _prompt_phone(inner, state)
                return
            if bot:
                await ensure_country_notice(db_user, session, bot)
            return await handler(event, data)

        if not settings.tgrass_code and not settings.botohub_key:
            # No providers configured — cannot verify subscriptions.
            # Do not silently pass the user through to captcha, as this would
            # allow bypassing the sponsor wall entirely without any real check.
            # The sponsor wall is disabled functionally, so pass through safely
            # only if sponsors_verified was already set by an admin action.
            # In normal flow this branch is only hit if a leftover "sponsor_check"
            # button is pressed after the providers were removed from config.
            if isinstance(inner, CallbackQuery):
                await inner.answer(
                    "⚠️ Проверка подписок временно недоступна.",
                    show_alert=True,
                )
            return

        from bot.services.botohub import check_botohub
        from bot.services.tgrass import check_tgrass

        tgrass_result, botohub_result = await asyncio.gather(
            check_tgrass(
                db_user.user_id,
                settings.tgrass_code,
                is_premium=bool(
                    isinstance(inner, Message)
                    and inner.from_user
                    and inner.from_user.is_premium
                    or isinstance(inner, CallbackQuery)
                    and inner.from_user
                    and inner.from_user.is_premium
                ),
                username=(
                    inner.from_user.username
                    if inner.from_user else None
                ),
                lang=(
                    inner.from_user.language_code or "ru"
                    if inner.from_user else "ru"
                ),
            ),
            check_botohub(db_user.user_id, settings.botohub_key),
            return_exceptions=True,
        )
        logger.info(
            "WALL uid=%s tgrass=%s botohub=%s",
            db_user.user_id,
            type(tgrass_result).__name__,
            type(botohub_result).__name__,
        )

        if all_configured_integrations_failed(
            tgrass_configured=bool(settings.tgrass_code),
            tgrass_result=tgrass_result,
            botohub_configured=bool(settings.botohub_key),
            botohub_result=botohub_result,
        ):
            await _show_retry(inner)
            return

        wave_size = min(
            10,
            max(1, await SettingsRepository(session).get_int(
                "sponsor_max_channels",
                10,
            )),
        )
        wave_state = evaluate_waves(
            db_user,
            tgrass_result=tgrass_result,
            botohub_result=botohub_result,
            wave_size=wave_size,
        )
        await session.commit()

        if wave_state.status == "unavailable":
            await _show_retry(inner)
            return
        if wave_state.status == "pending":
            await _show_wave(
                inner,
                wave=wave_state.wave,
                total_waves=wave_state.total_waves,
                items=wave_state.items or [],
            )
            return

        # Sponsor waves are complete; allow access.
        return await handler(event, data)
