import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import func, or_, select

from bot.config import get_settings
from bot.database.engine import SessionFactory
from bot.database.models import User

logger = logging.getLogger(__name__)
settings = get_settings()

_TICK_SECONDS = 1800  # 30 min -- frequent enough to catch the 24h mark
_INACTIVITY_WINDOW = timedelta(hours=24)
_SEND_DELAY_SECONDS = 0.05  # ~20 messages/sec, same pacing as broadcast.py

_REMINDER_TEXT = (
    "👋 Ты не приглашал рефералов уже сутки!\n\n"
    "Напоминаем: за каждого друга, который подпишется на спонсоров, "
    "ты получаешь RP⭐️ — а чем больше он подпишется, тем выше награда "
    "(вплоть до 18 RP⭐️ за одного реферала). Плюс бонусы за каждые "
    "10 приглашённых и статус VIP от 50.\n\n"
    "Твоя реферальная ссылка:\n<code>{link}</code>"
)


async def _send_reminders(bot: Bot) -> None:
    async with SessionFactory() as session:
        # One row per referrer who has ever brought in at least one
        # referral, paired with the timestamp of their MOST RECENT one.
        last_referral = (
            select(
                User.referrer_id.label("referrer_id"),
                func.max(User.created_at).label("last_referral_at"),
            )
            .where(User.referrer_id.is_not(None))
            .group_by(User.referrer_id)
            .subquery()
        )
        cutoff = datetime.utcnow() - _INACTIVITY_WINDOW
        result = await session.execute(
            select(User, last_referral.c.last_referral_at)
            .join(last_referral, last_referral.c.referrer_id == User.user_id)
            .where(
                last_referral.c.last_referral_at <= cutoff,
                User.is_blocked.is_(False),
                # Only fires once per inactivity streak -- a referrer who
                # brings in someone NEW after being reminded gets a fresh
                # last_referral_at higher than the stored reminder marker,
                # making them eligible again next time THAT streak goes
                # stale too. Without this every 30-min tick would re-spam
                # the same still-inactive referrer forever.
                or_(
                    User.last_referral_reminder_sent_at.is_(None),
                    User.last_referral_reminder_sent_at < last_referral.c.last_referral_at,
                ),
            )
        )
        rows = result.all()
        if not rows:
            return

        for user, last_referral_at in rows:
            link = f"https://t.me/{settings.bot_username}?start=ref_{user.user_id}"
            try:
                await bot.send_message(
                    user.user_id,
                    _REMINDER_TEXT.format(link=link),
                    parse_mode="HTML",
                )
            except Exception:
                # A blocked/deactivated account must not stop the rest
                # of the batch, and must not keep retrying forever --
                # still mark it reminded-for-this-streak below.
                pass
            user.last_referral_reminder_sent_at = last_referral_at
            await session.commit()
            await asyncio.sleep(_SEND_DELAY_SECONDS)

        logger.info("Referral reminder scheduler: sent %d reminder(s)", len(rows))


async def referral_reminder_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(_TICK_SECONDS)
        try:
            await _send_reminders(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Referral reminder scheduler error")
