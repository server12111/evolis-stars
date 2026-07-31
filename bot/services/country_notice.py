import logging

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository

logger = logging.getLogger(__name__)

COUNTRY_NOTICE_TEXT = (
    "🌍 <b>Страны, с которых можно приглашать пользователей</b>\n"
    "✅ Реферальная система засчитывает приглашения из следующих стран:\n\n"
    "🇺🇸 +1 — США (NANP)\n"
    "🇷🇺🇰🇿 +7 — Россия / Казахстан\n"
    "🇧🇷 +55 — Бразилия\n"
    "🇨🇴 +57 — Колумбия\n"
    "🇲🇲 +95 — Мьянма\n"
    "🇲🇩 +373 — Молдова\n"
    "🇦🇲 +374 — Армения\n"
    "🇧🇾 +375 — Беларусь\n"
    "🇺🇦 +380 — Украина\n"
    "🇹🇯 +992 — Таджикистан\n"
    "🇹🇲 +993 — Туркменистан\n"
    "🇦🇿 +994 — Азербайджан\n"
    "🇰🇬 +996 — Кыргызстан\n"
    "🇺🇿 +998 — Узбекистан\n\n"
    "❌ Все остальные страны не засчитываются в реферальной системе."
)


async def ensure_country_notice(
    user: User,
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Send the country allowlist once and keep retrying the pin if needed."""
    if not user.sponsors_verified:
        return
        
    phone_enabled = await SettingsRepository(session).get_bool(
        "phone_verification_enabled",
        True,
    )
    if phone_enabled and not user.phone_verified:
        return

    if user.country_notice_message_id is None:
        try:
            notice = await bot.send_message(
                user.user_id,
                COUNTRY_NOTICE_TEXT,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning(
                "Failed to send country notice to %s: %s",
                user.user_id,
                exc,
            )
            return
        user.country_notice_message_id = notice.message_id
        await session.commit()

    if user.country_notice_pinned:
        return

    try:
        await bot.pin_chat_message(
            chat_id=user.user_id,
            message_id=user.country_notice_message_id,
            disable_notification=True,
        )
    except Exception as exc:
        logger.warning(
            "Failed to pin country notice for %s: %s",
            user.user_id,
            exc,
        )
        return

    user.country_notice_pinned = True
    await session.commit()
