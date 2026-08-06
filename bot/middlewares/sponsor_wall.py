import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot, BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository
from bot.services.referral import check_referral_reward, notify_user_sponsors_verified
from bot.services.sponsor_results import all_configured_integrations_failed
from bot.services.sponsor_waves import (
    SponsorWaveState,
    evaluate_waves,
    sponsor_wave_markup,
    sponsor_wave_text,
)
from bot.services.telegram_chat import is_subscribed, telegram_chat_id

settings = get_settings()
logger = logging.getLogger(__name__)


def any_sponsor_provider_configured() -> bool:
    return bool(
        settings.tgrass_code
        or settings.botohub_key
        or settings.traffy_key
        or settings.flyerhub_op_key
    )


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
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            # Pressing "check" without actually subscribing to anything new
            # re-renders the exact same wave — Telegram rejects the edit as
            # a no-op change. That must NOT fall through to the generic
            # resend-as-a-new-message path below, or every repeat press
            # spams another duplicate wave bubble into the chat.
            await inner.answer("Пока ничего не изменилось.")
            return
        await inner.message.answer(
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


async def _check_membership_cached(
    bot: Bot, chat_id: str | int, user_id: int, cache: dict,
) -> bool | None:
    """A single live bot.get_chat_member lookup per (chat, user) per
    evaluation pass, shared between _drop_confirmed_subscriptions and
    _reinstate_expired_pinned_sponsors — both independently re-verify
    membership for their own reasons, and without this cache the same
    sponsor chat could be queried twice (once per function) in the same
    check. Returns None when the lookup itself failed (unknown, not
    "unsubscribed")."""
    if chat_id in cache:
        return cache[chat_id]
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return None
    result = is_subscribed(member)
    cache[chat_id] = result
    return result


async def _drop_confirmed_subscriptions(
    bot: Bot | None, user_id: int, provider_result: list[dict] | BaseException | None, cache: dict,
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
        if item.get("kind") == "trust":
            # Not a real Telegram membership (e.g. FlyerHub "give boost" /
            # "follow link" / "perform action") -- get_chat_member can't
            # tell whether the actual action happened, only whether the
            # user is a chat member, so never let it override the
            # provider's own completion verdict for these.
            return item
        chat_id = telegram_chat_id(item.get("url") or item.get("link"))
        if chat_id is None:
            return item
        subscribed = await _check_membership_cached(bot, chat_id, user_id, cache)
        if subscribed is None:
            return item
        return None if subscribed else item

    results = await asyncio.gather(
        *(_check(item) for item in provider_result), return_exceptions=True,
    )
    return [item for item in results if isinstance(item, dict)]


async def _reinstate_expired_pinned_sponsors(
    bot: Bot | None, db_user: User, results: dict[str, list[dict] | BaseException | None], cache: dict,
) -> None:
    """Providers pin a rotating batch of offers to a user for a limited
    window (BotoHub: ~3 minutes, per its own docs) and can legitimately
    stop mentioning a sponsor once that window passes or the batch
    rotates — NOT because the user subscribed, just because the provider
    moved on. evaluate_waves() otherwise reads "this saved sponsor is
    absent from the fresh provider batch" as "subscribed", which lets a
    wave silently pass a user who never joined anything if they wait past
    the pin window before pressing the check button. For every saved
    sponsor with a t.me link that's missing from its provider's fresh
    batch, independently confirm real membership via our own bot (same
    don't-trust-a-rotating-batch-alone principle as
    _drop_confirmed_subscriptions, applied in the opposite direction) and
    put it back in that provider's unsubscribed list if the user still
    isn't actually a member."""
    if bot is None or db_user.sponsor_wave not in (1, 2):
        return

    from bot.services.sponsor_waves import _current_items, _decorate, _key

    for item in _current_items(db_user):
        provider = str(item.get("provider", ""))
        provider_result = results.get(provider)
        if not isinstance(provider_result, list):
            continue
        if _key(item) in {_key(decorated) for decorated in _decorate(provider_result, provider)}:
            continue  # provider already reports it as unsubscribed — nothing to do

        chat_id = telegram_chat_id(item.get("url"))
        if chat_id is None:
            continue  # not a t.me link — no independent way to verify a web sponsor
        subscribed = await _check_membership_cached(bot, chat_id, db_user.user_id, cache)
        # Reinstate unless our own bot can POSITIVELY confirm membership.
        # For most sponsor channels the bot has no visibility at all (it's
        # a third-party channel it was never added to), so this lookup
        # comes back None far more often than False — treating None as
        # "fine, drop it" (the old behaviour) let a sponsor silently leave
        # the wave just because the provider's rotating batch stopped
        # mentioning it, with the user never having subscribed to anything.
        if subscribed is not True:
            provider_result.append(item)


async def _recheck_traffy(api_key: str, user_id: int, saved: list[dict]) -> list[dict] | None:
    """Re-check previously-issued Traffy assignment_ids by id -- NOT by
    re-fetching a fresh /tasks batch, which would hand out brand-new
    assignment_ids and silently grow/replace the frozen wave every time
    the user presses "check"."""
    from bot.services.traffy import check_traffy_tasks

    refs = [str(item.get("ref", "")) for item in saved if item.get("ref")]
    if not refs:
        return []
    statuses = await check_traffy_tasks(api_key, user_id, refs)
    if statuses is None:
        return None
    return [item for item in saved if not statuses.get(str(item.get("ref", "")), False)]


async def _recheck_flyerhub(api_key: str, saved: list[dict]) -> list[dict] | None:
    """Re-check previously-issued FlyerHub signatures by signature (same
    non-re-fetch reasoning as _recheck_traffy above). fh_check_task_op has
    no batch form, so signatures are checked concurrently one call each; a
    signature whose call itself fails is left pending rather than treated
    as complete, but doesn't abort the whole recheck unless every single
    one fails."""
    from bot.services.flyerhub import fh_check_task_op

    refs = [str(item.get("ref", "")) for item in saved if item.get("ref")]
    if not refs:
        return []
    outcomes = await asyncio.gather(
        *(fh_check_task_op(api_key, ref) for ref in refs), return_exceptions=True,
    )
    statuses: dict[str, bool] = {}
    any_resolved = False
    for ref, outcome in zip(refs, outcomes):
        if isinstance(outcome, BaseException) or outcome is None:
            continue
        any_resolved = True
        statuses[ref] = outcome == "complete"
    if not any_resolved:
        return None
    return [item for item in saved if not statuses.get(str(item.get("ref", "")), False)]


async def get_pending_sponsor_items(
    inner: Message | CallbackQuery,
    db_user: User,
    session: AsyncSession,
    bot: Bot | None = None,
) -> list[dict]:
    """The sponsors the user is genuinely still unsubscribed to right now —
    a fresh provider check, same as what a wave render would show. Used by
    the "skip sponsors" flow so the price always matches what's actually on
    screen (the frozen wave's full size is NOT the right count once the
    user has already subscribed to some of it)."""
    # skip_when_complete=True: a stale "sponsor_skip" button pressed after
    # the user already finished normally must report nothing left to skip
    # instead of hitting every provider again — see the parameter's own
    # docstring below for why this must NOT apply to the periodic recheck.
    wave_state = await _evaluate_wave_state(
        inner, db_user, session, bot, skip_when_complete=True, top_up=False,
    )
    if wave_state.status != "pending":
        return []
    return wave_state.items or []


async def _evaluate_wave_state(
    inner: Message | CallbackQuery,
    db_user: User,
    session: AsyncSession,
    bot: Bot | None = None,
    skip_when_complete: bool = False,
    top_up: bool = True,
) -> SponsorWaveState:
    """Checks every configured sponsor provider fresh and evaluates the
    current wave against it — the shared core behind both
    run_sponsor_wall_check (which also renders/commits) and
    get_pending_sponsor_items (which only needs the resulting item list).

    skip_when_complete short-circuits to "complete" immediately for a
    sponsor_wave==3 user, with no provider calls at all — correct for
    get_pending_sponsor_items (sponsor_skip is exempt from
    SponsorWallMiddleware's own sponsors_verified check, so a stale skip
    button pressed after the user already finished normally must not
    re-hit every provider just to report nothing left to skip). It must
    stay False for run_sponsor_wall_check's normal path: sponsor_recheck_
    scheduler periodically flips sponsors_verified back to False for
    already-complete users specifically so the NEXT interaction re-checks
    providers for sponsors that weren't there before — short-circuiting
    here too would make that recheck a permanent no-op forever after the
    first time a user ever reaches wave 3, silently defeating it.

    top_up mirrors this same read-only requirement for get_pending_
    sponsor_items: evaluate_waves() can grow and persist a bigger wave
    when sponsors resolve (see its own docstring), which is exactly right
    for a real check but wrong for a price quote — merely opening the
    "skip sponsors" dialog must not silently inflate what the user owes.
    """
    if db_user.sponsor_wave == 3 and skip_when_complete:
        return SponsorWaveState("complete")

    from bot.services.botohub import check_botohub
    from bot.services.tgrass import check_tgrass
    from bot.services.traffy import get_traffy_tasks
    from bot.services.flyerhub import fh_get_tasks_op, fh_task_to_sponsor_item
    from bot.services.sponsor_waves import _current_items

    membership_cache: dict = {}
    wave_frozen = db_user.sponsor_wave in (1, 2)

    wave_size = min(
        20,
        max(1, await SettingsRepository(session).get_int(
            "sponsor_max_channels",
            10,
        )),
    )

    from bot.database.repositories.blocked_sponsor import BlockedSponsorRepository

    blocked_sponsor_repo = BlockedSponsorRepository(session)
    blocked_urls = frozenset(await blocked_sponsor_repo.url_key_set())
    blocked_domains = frozenset(await blocked_sponsor_repo.domain_key_set())

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
        _drop_confirmed_subscriptions(bot, db_user.user_id, tgrass_result, membership_cache),
        _drop_confirmed_subscriptions(bot, db_user.user_id, botohub_result, membership_cache),
    )

    # Traffy and FlyerHub are both "get once, re-check by id" providers
    # (assignment_id / signature) rather than tgrass/botohub's stateless
    # "get = current unsubscribed list" model — re-fetching a fresh batch
    # from either on every "check" press would hand out brand-new ids and
    # silently grow/replace an already-frozen wave. Once the wave is
    # frozen, only the saved ids for that provider are re-checked; a fresh
    # batch is only pulled while the wave is still being assembled.
    if settings.traffy_key:
        if wave_frozen:
            saved_traffy = [
                item for item in _current_items(db_user)
                if str(item.get("provider", "")) == "traffy"
            ]
            traffy_result = await _recheck_traffy(settings.traffy_key, db_user.user_id, saved_traffy)
        else:
            traffy_result = await get_traffy_tasks(
                settings.traffy_key,
                db_user.user_id,
                limit=wave_size,
                first_name=(inner.from_user.first_name if inner.from_user else None),
                username=(inner.from_user.username if inner.from_user else None),
                language_code=(inner.from_user.language_code if inner.from_user else None),
            )
        traffy_result = await _drop_confirmed_subscriptions(
            bot, db_user.user_id, traffy_result, membership_cache,
        )
    else:
        traffy_result = []

    if settings.flyerhub_op_key:
        if wave_frozen:
            saved_flyerhub = [
                item for item in _current_items(db_user)
                if str(item.get("provider", "")) == "flyerhub"
            ]
            flyerhub_result = await _recheck_flyerhub(settings.flyerhub_op_key, saved_flyerhub)
        else:
            raw_tasks = await fh_get_tasks_op(
                settings.flyerhub_op_key,
                db_user.user_id,
                limit=min(10, wave_size),
            )
            if raw_tasks is None:
                flyerhub_result = None
            else:
                flyerhub_result = [
                    item for raw in raw_tasks
                    if isinstance(raw, dict) and (item := fh_task_to_sponsor_item(raw)) is not None
                ]
        # kind="trust" items (boosts/links/actions) are left untouched by
        # this — see _drop_confirmed_subscriptions' own check.
        flyerhub_result = await _drop_confirmed_subscriptions(
            bot, db_user.user_id, flyerhub_result, membership_cache,
        )
    else:
        flyerhub_result = []

    logger.info(
        "WALL uid=%s tgrass=%s botohub=%s traffy=%s flyerhub=%s",
        db_user.user_id,
        _describe_result(tgrass_result),
        _describe_result(botohub_result),
        _describe_result(traffy_result),
        _describe_result(flyerhub_result),
    )

    if all_configured_integrations_failed(
        tgrass_configured=bool(settings.tgrass_code),
        tgrass_result=tgrass_result,
        botohub_configured=bool(settings.botohub_key),
        botohub_result=botohub_result,
        traffy_configured=bool(settings.traffy_key),
        traffy_result=traffy_result,
        flyerhub_configured=bool(settings.flyerhub_op_key),
        flyerhub_result=flyerhub_result,
    ):
        return SponsorWaveState("unavailable")

    # Traffy/FlyerHub are deliberately excluded here — the reinstate
    # concern only applies to tgrass/botohub's rotating "fresh batch"
    # model (see _reinstate_expired_pinned_sponsors' own docstring); a
    # saved Traffy/FlyerHub item is always re-checked by its own id above,
    # so it can never "silently disappear" the way a rotated batch can.
    await _reinstate_expired_pinned_sponsors(
        bot, db_user, {"tgrass": tgrass_result, "botohub": botohub_result},
        membership_cache,
    )

    wave_state = evaluate_waves(
        db_user,
        tgrass_result=tgrass_result,
        botohub_result=botohub_result,
        traffy_result=traffy_result,
        flyerhub_result=flyerhub_result,
        traffy_configured=bool(settings.traffy_key),
        flyerhub_configured=bool(settings.flyerhub_op_key),
        wave_size=wave_size,
        top_up=top_up,
        blocked_urls=blocked_urls,
        blocked_domains=blocked_domains,
    )
    await session.commit()
    return wave_state


async def run_sponsor_wall_check(
    inner: Message | CallbackQuery,
    db_user: User,
    session: AsyncSession,
    bot: Bot | None = None,
) -> bool:
    """Check all configured sponsor providers and show the current wave.

    Assumes the caller already verified at least one provider is configured.
    Returns True when every wave is complete and the caller should proceed;
    False when a wave or a retry message was already sent to the user.
    """
    wave_state = await _evaluate_wave_state(inner, db_user, session, bot)

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

        if not any_sponsor_provider_configured():
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
