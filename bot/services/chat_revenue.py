from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Chat
from bot.database.repositories.chat_ads import ChatAdRepository
from bot.database.repositories.link_clicks import LinkClickRepository
from bot.services.chat_eligibility import credit_stars

_VIEWS_PER_PAYOUT = 1000
_VIEWS_PAYOUT = Decimal("0.5")
_CLICK_BONUS_THRESHOLD = 400
_CLICK_BONUS = Decimal("4")


async def settle_chat_ad_revenue(session: AsyncSession, chat: Chat) -> None:
    """Idempotent — safe to call after every new attributed send/click;
    Chat.ads_revenue_paid_thresholds/ads_bonus_paid make sure a threshold
    already paid out is never paid again. Called from both a background
    sweep (chat_ad_scheduler.py) and a live per-click handler
    (link_click.py, chat-type agnostic, no per-user lock) — two calls for
    the same chat can run concurrently, so each payout is claimed with an
    atomic conditional UPDATE (matching the previously-read value) before
    crediting, instead of a plain read-then-write, to avoid double-paying
    the same threshold/bonus."""
    if chat.owner_user_id is None:
        return

    ad_repo = ChatAdRepository(session)
    total_sends = await ad_repo.count_for_chat(chat.chat_id)
    eligible_thresholds = total_sends // _VIEWS_PER_PAYOUT
    new_thresholds = eligible_thresholds - chat.ads_revenue_paid_thresholds
    if new_thresholds > 0:
        # A 0-row match here means someone else (another concurrent call for
        # this same chat) already claimed this threshold — that UPDATE
        # changed nothing, so unlike the rowcount-mismatch pattern used for
        # game rounds elsewhere, there is nothing to roll back and no need
        # to: rolling back here would expire every object in this session's
        # identity map (including `chat` and, in the scheduler's loop, every
        # other not-yet-processed Chat) and crash the next plain attribute
        # read with MissingGreenlet.
        claim = await session.execute(
            update(Chat)
            .where(
                Chat.chat_id == chat.chat_id,
                Chat.ads_revenue_paid_thresholds == chat.ads_revenue_paid_thresholds,
            )
            .values(ads_revenue_paid_thresholds=eligible_thresholds)
        )
        if claim.rowcount == 1:
            await credit_stars(session, chat.owner_user_id, _VIEWS_PAYOUT * new_thresholds)
            chat.ads_revenue_paid_thresholds = eligible_thresholds

    if not chat.ads_bonus_paid:
        click_repo = LinkClickRepository(session)
        clicks = await click_repo.count_for_chat(chat.chat_id)
        if clicks >= _CLICK_BONUS_THRESHOLD:
            claim = await session.execute(
                update(Chat)
                .where(Chat.chat_id == chat.chat_id, Chat.ads_bonus_paid.is_(False))
                .values(ads_bonus_paid=True)
            )
            if claim.rowcount == 1:
                await credit_stars(session, chat.owner_user_id, _CLICK_BONUS)
                chat.ads_bonus_paid = True
