import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.chat_wall_integration import ChatWallIntegrationRepository
from bot.services.chat_eligibility import credit_stars
from bot.services.sponsor_waves import (
    SponsorWaveState,
    WaveFields,
    _current_items,
    _identity_key,
    is_sponsor_blocked,
    normalize_sponsor_url,
)

logger = logging.getLogger(__name__)

# A second, independent wave engine run (see bot.services.sponsor_waves.
# WaveFields) pointed at User.wall_integration_wave* instead of the /start
# onboarding wall's sponsor_wave* -- global per user, not per chat: the same
# tgrass/botohub/traffy/flyerhub offer pool applies regardless of which
# walled chat triggered the check.
WALL_INTEGRATION_FIELDS = WaveFields(
    wave="wall_integration_wave",
    one="wall_integration_wave_one",
    two="wall_integration_wave_two",
)

INTEGRATION_REWARD = Decimal("1")


def integration_item_key(item: dict) -> str:
    """Stable per-item identity for the completion ledger -- traffy/
    flyerhub's own opaque ref (assignment_id/signature) when present
    (stateful, id-identified providers), otherwise the normalized URL
    (tgrass/botohub -- stateless, URL-identified)."""
    ref = str(item.get("ref", "")).strip()
    if ref:
        return ref
    return normalize_sponsor_url(item.get("url", ""))


async def pending_integration_items(user: User, session: AsyncSession) -> list[dict]:
    """DB-only: which of the user's currently-frozen integration-wave items
    are still not completed -- the mandatory gate's hot path, no provider
    calls. Empty when the wave is unset (0, never frozen) or fully
    resolved (3).

    Also drops anything an admin has since blocklisted: without this, an
    item blocked after being frozen into a user's wave would never be
    cleared on this DB-only path (only evaluate_and_credit_integration_wave's
    live path applies the blocklist), leaving that user permanently unable
    to post until they happen to press "Проверить" themselves. The
    blocklist lookup is still just a DB read, not a provider call, so this
    doesn't change the hot path's cost profile."""
    items = _current_items(user, WALL_INTEGRATION_FIELDS)
    if not items:
        return []

    from bot.database.repositories.blocked_sponsor import BlockedSponsorRepository

    blocked_sponsor_repo = BlockedSponsorRepository(session)
    blocked_urls = frozenset(await blocked_sponsor_repo.url_key_set())
    blocked_domains = frozenset(await blocked_sponsor_repo.domain_key_set())
    items = [
        item for item in items
        if not is_sponsor_blocked(item.get("url", ""), blocked_urls, blocked_domains)
    ]
    if not items:
        return []

    repo = ChatWallIntegrationRepository(session)
    completed = await repo.completed_pairs(user.user_id)
    return [
        item for item in items
        if (str(item.get("provider", "")), integration_item_key(item)) not in completed
    ]


async def evaluate_and_credit_integration_wave(
    inner: Message | CallbackQuery, user: User, session: AsyncSession, bot: Bot | None,
) -> tuple[SponsorWaveState, int]:
    """The one live-provider round either the gate's first-ever freeze for
    a user (wall_integration_wave == 0) or an explicit "Проверить" press is
    allowed to spend -- reuses the exact same provider-calling engine as
    the /start wall (bot.middlewares.sponsor_wall.evaluate_provider_wave),
    just pointed at WALL_INTEGRATION_FIELDS. Credits 1 RP⭐️ for every item
    that resolved (confirmed subscribed) this round (correctly a no-op on
    a fresh freeze, since nothing was shown before this call to have
    possibly resolved yet) and returns how many were newly credited
    alongside the resulting wave state.

    A no-op (no provider call at all) once the wave is already fully
    resolved (3) -- without this, a stale/repeat "Проверить" press (e.g.
    in a different walled chat) would re-run initialize_waves against
    fresh provider results and could re-freeze a brand-new pending wave,
    re-blocking an already-done user. Mirrors run_sponsor_wall_check's own
    sponsors_verified short-circuit for the /start wall."""
    if getattr(user, WALL_INTEGRATION_FIELDS.wave) == 3:
        return SponsorWaveState("complete"), 0

    from bot.database.repositories.blocked_sponsor import BlockedSponsorRepository
    from bot.middlewares.sponsor_wall import evaluate_provider_wave

    before_items = _current_items(user, WALL_INTEGRATION_FIELDS)
    wave_state = await evaluate_provider_wave(inner, user, session, bot, fields=WALL_INTEGRATION_FIELDS)
    if wave_state.status == "unavailable" or not before_items:
        return wave_state, 0

    blocked_sponsor_repo = BlockedSponsorRepository(session)
    blocked_urls = frozenset(await blocked_sponsor_repo.url_key_set())
    blocked_domains = frozenset(await blocked_sponsor_repo.domain_key_set())

    still_pending = {_identity_key(item) for item in (wave_state.items or [])}
    repo = ChatWallIntegrationRepository(session)
    newly_credited = 0
    for item in before_items:
        if _identity_key(item) in still_pending:
            continue
        if is_sponsor_blocked(item.get("url", ""), blocked_urls, blocked_domains):
            # Auto-dropped by evaluate_waves because it's now admin-
            # blocklisted, not because the user actually subscribed --
            # must never be paid for.
            continue
        provider = str(item.get("provider", ""))
        item_key = integration_item_key(item)
        if await repo.mark_completed(user.user_id, provider, item_key):
            await credit_stars(session, user.user_id, INTEGRATION_REWARD)
            newly_credited += 1
    return wave_state, newly_credited


async def safe_pending_integration_items(user: User, session: AsyncSession) -> tuple[list[dict], bool]:
    """pending_integration_items, guarded: an unexpected exception (a
    genuine DB error, not a provider call -- this path never touches a
    provider) must not crash the caller (the per-message gate's hot path,
    hit far more often than the wave==0 freeze path since a user's wave
    only equals 0 once, ever). Returns (items, unavailable) -- unavailable
    mirrors evaluate_and_credit_integration_wave's own "unavailable"
    status so callers can treat both paths identically (still blocking,
    never silently passing the wall)."""
    try:
        return await pending_integration_items(user, session), False
    except Exception:
        logger.exception("WALL integration pending-check failed uid=%s", user.user_id)
        await session.rollback()
        return [], True


async def safe_evaluate_and_credit_integration_wave(
    inner: Message | CallbackQuery, user: User, session: AsyncSession, bot: Bot | None,
) -> tuple[SponsorWaveState, int]:
    """evaluate_and_credit_integration_wave, guarded: an unexpected
    exception (a real provider/network failure, not the internal ones
    evaluate_provider_wave already swallows) must not crash the caller --
    from the user's side that looks like pressing "Проверить" and getting
    no response at all. Degrades to SponsorWaveState("unavailable") (still
    blocking, never silently passing the wall) instead, and rolls back the
    session so a mid-write failure (e.g. credit_stars) can't leave it
    unusable for whatever the caller does next."""
    try:
        return await evaluate_and_credit_integration_wave(inner, user, session, bot)
    except Exception:
        logger.exception("WALL integration eval failed uid=%s", user.user_id)
        await session.rollback()
        return SponsorWaveState("unavailable"), 0
