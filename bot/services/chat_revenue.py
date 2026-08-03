from decimal import Decimal

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
    already paid out is never paid again."""
    if chat.owner_user_id is None:
        return

    ad_repo = ChatAdRepository(session)
    total_sends = await ad_repo.count_for_chat(chat.chat_id)
    eligible_thresholds = total_sends // _VIEWS_PER_PAYOUT
    new_thresholds = eligible_thresholds - chat.ads_revenue_paid_thresholds
    if new_thresholds > 0:
        await credit_stars(session, chat.owner_user_id, _VIEWS_PAYOUT * new_thresholds)
        chat.ads_revenue_paid_thresholds = eligible_thresholds
        await session.commit()

    if not chat.ads_bonus_paid:
        click_repo = LinkClickRepository(session)
        clicks = await click_repo.count_for_chat(chat.chat_id)
        if clicks >= _CLICK_BONUS_THRESHOLD:
            await credit_stars(session, chat.owner_user_id, _CLICK_BONUS)
            chat.ads_bonus_paid = True
            await session.commit()
