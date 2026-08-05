import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot, BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository
from bot.services.referral import check_referral_reward, notify_user_sponsors_verified
from bot.services.sponsor_results import all_configured_integrations_failed
from bot.services.sponsor_waves import (
    evaluate_waves,
    sponsor_wave_markup,
    sponsor_wave_text,
)
from bot.services.telegram_chat import is_subscribed, telegram_chat_id

settings = get_settings()
logger = logging.getLogger(__name__)

_BYPASS_PREFIXES = (
    "/start",
    "/admin",
    "admin:",
    "wall_check",
    "sponsor_check",
    "sponsor_skip",
    "tos_accept",
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


def _describe_result(result: list[dict] | BaseException | None) -> str:
    """Log-friendly summary: a count for a normal result, ERR:<type> for a
    gathered exception, or "None" for an explicit failure sentinel — so a
    legitimately empty provider response is distinguishable in the logs from
    a crash or an outage."""
    if isinstance(result, list):
        return str(len(result))
    if isinstance(result, BaseException):
        return f"ERR:{type(result).__name__}"
    return "None"


async def _drop_confirmed_subscriptions(
    bot: Bot | None, user_id: int, provider_result: list[dict] | BaseException | None,
) -> list[dict] | BaseException | None:
    """Providers (tgrass/botohub) sometimes lag behind Telegram's own
    membership state — propagation delay, a rotated task set, etc. — and
    keep reporting a t.me channel as unsubscribed even after the user has
    genuinely joined it. Independently re-check each t.me link ourselves
    and drop it from the "still unsubscribed" list when our own bot
    confirms membership, so a stale provider verdict can never cause a
    false "not subscribed". Mirrors the same don't-trust-the-provider-
    blindly principle already used for reward payouts (see
    referral.py::_verify_tg_subscriptions), applied to the other side of
    the check. Non-t.me links and anything the live lookup can't resolve
    are left exactly as the provider reported them."""
    if bot is None or not isinstance(provider_result, list):
        return provider_result

    async def _check(item: dict) -> dict | None:
        chat_id = telegram_chat_id(item.get("url") or item.get("link"))
        if chat_id is None:
            return item
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            return item
        return None if is_subscribed(member) else item

    results = await asyncio.gather(
        *(_check(item) for item in provider_result), return_exceptions=True,
    )
    return [item for item in results if isinstance(item, dict)]


async def run_sponsor_wall_check(
    inner: Message | CallbackQuery,
    db_user: User,
    session: AsyncSession,
    bot: Bot | None = None,
) -> bool:
    """Check all configured sponsor providers and show the current wave.

    Assumes the caller already verified at least one provider is configured.
    PiarFlow is only pulled in when tgrass+botohub alone don't cover the
    configured reward minimum (or when a wave already frozen a PiarFlow
    sponsor that still needs a subscription re-check) — it tops up the free
    exchange providers rather than always being shown.
    Returns True when every wave is complete and the caller should proceed;
    False when a wave or a retry message was already sent to the user.
    """
    from bot.services.botohub import check_botohub
    from bot.services.tgrass import check_tgrass
    from bot.services.piarflow import get_sponsors, check_sponsors
    from bot.services.sponsor_waves import _current_items, _url_key
    from bot.services.referral import get_min_sponsors_for_reward

    tgrass_result, botohub_result = await asyncio.gather(
        check_tgrass(
            db_user.user_id,
            settings.tgrass_code,
            is_premium=bool(inner.from_user and inner.from_user.is_premium),
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
    tgrass_result, botohub_result = await asyncio.gather(
        _drop_confirmed_subscriptions(bot, db_user.user_id, tgrass_result),
        _drop_confirmed_subscriptions(bot, db_user.user_id, botohub_result),
    )

    piarflow_needed = False
    piarflow_result: list[dict] | None = None

    if settings.piarflow_key:
        if db_user.sponsor_wave in (1, 2):
            # Wave already frozen — only re-check PiarFlow if it actually
            # contributed a sponsor to the currently active wave.
            saved_items = _current_items(db_user)
            piarflow_links = [
                str(item.get("url", "")) for item in saved_items
                if str(item.get("provider", "")) == "piarflow" and item.get("url")
            ]
            piarflow_needed = bool(piarflow_links)
            if piarflow_needed:
                # check_sponsors is the authoritative per-link subscription
                # verdict from PiarFlow. Trust it directly instead of
                # re-fetching a fresh batch via get_sponsors() — that's a
                # different endpoint for handing out NEW sponsor tasks, and
                # its contents don't reliably mean "still unsubscribed": an
                # empty/different response there let unsubscribed users
                # through the wave regardless of their real check_sponsors
                # status.
                all_subscribed = await check_sponsors(
                    settings.piarflow_key,
                    db_user.user_id,
                    piarflow_links,
                )
                piarflow_result = [] if all_subscribed else [
                    item for item in saved_items
                    if str(item.get("provider", "")) == "piarflow"
                ]
                # check_sponsors only returns one aggregate bool for the whole
                # batch, so a single stale/lagging PiarFlow verdict marks
                # EVERY piarflow sponsor in the wave as unsubscribed — with
                # no per-link signal to tell which one actually failed.
                # Independently re-check each via our own bot the same way
                # tgrass/botohub results already are, so a user who is
                # genuinely subscribed to everything isn't stuck because of
                # one provider-side false negative.
                piarflow_result = await _drop_confirmed_subscriptions(
                    bot, db_user.user_id, piarflow_result,
                )
        else:
            # Not yet frozen — top PiarFlow up only far enough to cover the
            # reward-eligibility minimum that tgrass+botohub didn't reach.
            free_urls: set[str] = set()
            for provider_result in (tgrass_result, botohub_result):
                if isinstance(provider_result, list):
                    free_urls.update(
                        url_key
                        for item in provider_result
                        if (url_key := _url_key(item))
                    )
            gap = await get_min_sponsors_for_reward(session) - len(free_urls)
            piarflow_needed = gap > 0
            if piarflow_needed:
                piarflow_result = await get_sponsors(
                    settings.piarflow_key,
                    db_user.user_id,
                    db_user.user_id,
                    max_sponsors=min(20, gap),
                )

    if not piarflow_needed:
        piarflow_result = []

    logger.info(
        "WALL uid=%s tgrass=%s botohub=%s piarflow_needed=%s piarflow=%s",
        db_user.user_id,
        _describe_result(tgrass_result),
        _describe_result(botohub_result),
        piarflow_needed,
        _describe_result(piarflow_result),
    )

    if all_configured_integrations_failed(
        tgrass_configured=bool(settings.tgrass_code),
        tgrass_result=tgrass_result,
        botohub_configured=bool(settings.botohub_key),
        botohub_result=botohub_result,
        piarflow_configured=piarflow_needed,
        piarflow_result=piarflow_result,
    ):
        await _show_retry(inner)
        return False

    wave_size = min(
        20,
        max(1, await SettingsRepository(session).get_int(
            "sponsor_max_channels",
            10,
        )),
    )
    wave_state = evaluate_waves(
        db_user,
        tgrass_result=tgrass_result,
        botohub_result=botohub_result,
        piarflow_result=piarflow_result,
        piarflow_configured=piarflow_needed,
        wave_size=wave_size,
    )
    await session.commit()

    if wave_state.status == "unavailable":
        await _show_retry(inner)
        return False
    if wave_state.status == "pending":
        await _show_wave(
            inner,
            wave=wave_state.wave,
            total_waves=wave_state.total_waves,
            items=wave_state.items or [],
        )
        return False

    # Sponsor waves are complete; allow access.
    return True


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

        # A Telegram Stars payment confirmation (see the sponsor-skip flow
        # in start.py) must always reach its handler — it's what actually
        # marks the wave passed, so blocking it here would leave the user
        # stuck at the wall despite having just paid to skip it.
        if isinstance(inner, Message) and inner.successful_payment is not None:
            return await handler(event, data)

        callback_data = inner.data if isinstance(inner, CallbackQuery) else None
        message_text = inner.text if isinstance(inner, Message) else None
        if _should_skip(callback_data, message_text):
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        state = data.get("state")
        if session is None or state is None:
            return await handler(event, data)

        if db_user.sponsors_verified:
            return await handler(event, data)

        if not settings.tgrass_code and not settings.botohub_key:
            # No providers configured — cannot verify subscriptions.
            # In normal flow this branch is only hit if a leftover
            # "sponsor_check" button is pressed after providers were removed
            # from config; /start already auto-verifies in that case.
            if isinstance(inner, CallbackQuery):
                await inner.answer(
                    "⚠️ Проверка подписок временно недоступна.",
                    show_alert=True,
                )
            return

        bot = data.get("bot")
        if not await run_sponsor_wall_check(inner, db_user, session, bot):
            return

        # The wave just resolved to "complete" outside the /start /
        # sponsor_check flows (e.g. right after the 10-minute recheck
        # scheduler reopened the wall) — persist the flag so subsequent
        # messages don't re-run the provider checks every single time, and
        # run the same first-time notify/reward hooks /start would have.
        if not db_user.sponsors_verified:
            db_user.sponsors_verified = True
            await session.commit()
            if bot is not None:
                await notify_user_sponsors_verified(db_user, session, bot)
                await check_referral_reward(db_user, session, bot)

        return await handler(event, data)
