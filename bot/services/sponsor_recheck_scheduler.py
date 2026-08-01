import asyncio
import logging

from aiogram import Bot
from sqlalchemy import update

from bot.database.engine import SessionFactory

logger = logging.getLogger(__name__)

RECHECK_INTERVAL_SECONDS = 600


async def sponsor_recheck_loop(bot: Bot) -> None:
    """Every ~10 minutes, re-open the sponsor wall for already-verified users.

    This only flips `sponsors_verified` back to False in bulk — it never
    calls tgrass/botohub/piarflow itself. The next time each user interacts
    with the bot, SponsorWallMiddleware transparently re-runs the existing
    (already tested) initialize_waves/evaluate_waves check against fresh
    provider data. If nothing changed, evaluate_waves resolves back to
    "complete" immediately with no message shown to the user; if a
    provider now has new sponsors, the user sees a wave with just those.
    check_referral_reward/notify_user_sponsors_verified are already gated
    by the permanent `referral_reward_given` flag, so repeated
    sponsors_verified cycles for an already-rewarded referral never pay out
    or notify twice.
    """
    while True:
        await asyncio.sleep(RECHECK_INTERVAL_SECONDS)
        try:
            async with SessionFactory() as session:
                from bot.database.models import User

                await session.execute(
                    update(User)
                    .where(User.sponsors_verified.is_(True), User.is_admin.is_(False))
                    .values(sponsors_verified=False)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
        except Exception:
            logger.exception("Sponsor recheck scheduler error")
