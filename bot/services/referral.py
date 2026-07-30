import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import escape

from aiogram import Bot
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import ReferralReactivation, User
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository

logger = logging.getLogger(__name__)

REFERRAL_RETURN_DAYS = 7
_STAR_STEP = Decimal("0.01")


def format_stars(value: Decimal | float) -> str:
    amount = Decimal(str(value)).quantize(_STAR_STEP, rounding=ROUND_HALF_UP)
    return f"{amount:.2f}".rstrip("0").rstrip(".")


async def get_referral_reward(session: AsyncSession) -> Decimal:
    raw_reward = await SettingsRepository(session).get("referral_reward", "3")
    try:
        ordinary_reward = Decimal(raw_reward)
    except (InvalidOperation, TypeError):
        ordinary_reward = Decimal("3")
    if not ordinary_reward.is_finite():
        ordinary_reward = Decimal("3")
    return max(Decimal("0"), ordinary_reward).quantize(
        _STAR_STEP,
        rounding=ROUND_HALF_UP,
    )


async def get_return_reward(session: AsyncSession) -> Decimal:
    ordinary_reward = await get_referral_reward(session)
    return (ordinary_reward / 2).quantize(_STAR_STEP, rounding=ROUND_HALF_UP)


async def reward_returning_referral(
    user: User,
    requested_referrer_id: int,
    previous_last_seen_at: datetime | None,
    session: AsyncSession,
    bot: Bot | None = None,
) -> Decimal | None:
    """Pay one half-reward when a qualified referral returns after 7 inactive days."""
    if (
        previous_last_seen_at is None
        or user.referrer_id != requested_referrer_id
        or not user.referral_counted
        or not user.referral_reward_given
    ):
        return None

    now = datetime.utcnow()
    if previous_last_seen_at > now - timedelta(days=REFERRAL_RETURN_DAYS):
        return None

    referrer = await UserRepository(session).get(requested_referrer_id)
    if not referrer or referrer.is_blocked:
        return None

    reward = await get_return_reward(session)
    if reward <= 0:
        return None

    referred_user_id = user.user_id
    session.add(
        ReferralReactivation(
            referred_user_id=referred_user_id,
            referrer_id=requested_referrer_id,
            inactive_since=previous_last_seen_at,
            returned_at=now,
            reward_amount=reward,
        )
    )
    try:
        await session.flush()
        # An SQL-side increment prevents lost rewards when different referrals
        # return to the same referrer at the same moment.
        await session.execute(
            update(User)
            .where(User.user_id == requested_referrer_id)
            .values(stars_balance=User.stars_balance + reward)
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # Rollback expires ORM objects. Refresh the middleware-provided user so
        # the rest of the /start handler can safely continue using it.
        await session.refresh(user)
        logger.info(
            "REFERRAL_RETURN uid=%d: duplicate return cycle ignored",
            referred_user_id,
        )
        return None

    logger.info(
        "REFERRAL_RETURN uid=%d referrer=%d inactive_since=%s reward=%s",
        referred_user_id,
        requested_referrer_id,
        previous_last_seen_at.isoformat(),
        reward,
    )
    if bot:
        username_display = (
            f"@{escape(user.username)}"
            if user.username
            else escape(user.first_name or str(user.user_id))
        )
        try:
            await bot.send_message(
                requested_referrer_id,
                "♻️ Ваш реферал "
                f"{username_display} вернулся по ссылке после {REFERRAL_RETURN_DAYS} дней "
                f"неактивности.\n\nНачислено <b>{format_stars(reward)} ⭐</b> — "
                "половина обычной награды.",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning(
                "Failed to notify referrer %s of referral return: %s",
                requested_referrer_id,
                exc,
            )
    return reward


async def check_referral_reward(user: User, session: AsyncSession, bot: Bot | None = None) -> None:
    """Check if this user has fulfilled conditions → pay referral reward to referrer once."""
    if user.referral_reward_given:
        logger.info("REFERRAL uid=%d: skip — reward already given", user.user_id)
        return
    if not user.referrer_id:
        logger.info("REFERRAL uid=%d: skip — no referrer_id", user.user_id)
        return

    repo = SettingsRepository(session)
    phone_enabled = await repo.get_bool("phone_verification_enabled", True)
    if phone_enabled and not user.phone_verified:
        logger.info("REFERRAL uid=%d: skip — phone_verified=False", user.user_id)
        return

    min_tasks = await repo.get_int("min_tasks_for_referral", 3)
    reward = await get_referral_reward(session)

    settings = get_settings()
    if (settings.tgrass_code or settings.botohub_key) and not user.sponsors_verified:
        logger.info("REFERRAL uid=%d: skip — sponsors_verified=False (tgrass=%r botohub=%r)",
                    user.user_id, bool(settings.tgrass_code), bool(settings.botohub_key))
        return
    if not phone_enabled and not user.referral_counted:
        await mark_referral_phone_accepted(
            user,
            session,
            bot,
            phone_check_enabled=False,
        )
    if user.tasks_completed_count < min_tasks:
        logger.info("REFERRAL uid=%d: skip — tasks %d < min %d",
                    user.user_id, user.tasks_completed_count, min_tasks)
        return

    logger.info("REFERRAL uid=%d: conditions met (tasks=%d sponsors=%s referrer=%d) → giving reward %.2f",
                user.user_id, user.tasks_completed_count, user.sponsors_verified, user.referrer_id, float(reward))

    # All conditions met — reward the referrer
    user_repo = UserRepository(session)
    referrer = await user_repo.get(user.referrer_id)
    if not referrer:
        logger.warning("REFERRAL uid=%d: referrer %d not found", user.user_id, user.referrer_id)
        return

    user.referral_reward_given = True
    await session.execute(
        update(User)
        .where(User.user_id == referrer.user_id)
        .values(stars_balance=User.stars_balance + reward)
    )
    await session.commit()

    username_display = (
        f"@{escape(user.username)}"
        if user.username
        else escape(user.first_name or str(user.user_id))
    )
    if bot:
        try:
            await bot.send_message(
                referrer.user_id,
                f"🎉 Вам начислено <b>{format_stars(reward)} ⭐</b> за пользователя {username_display}.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Failed to notify referrer %s: %s", referrer.user_id, e)


async def mark_referral_phone_accepted(
    user: User,
    session: AsyncSession,
    bot: Bot | None = None,
    *,
    phone_check_enabled: bool = True,
) -> None:
    """Count a referral exactly once, after the referred user's phone passes."""
    if not user.referrer_id or user.referral_counted:
        return

    referrer = await UserRepository(session).get(user.referrer_id)
    if not referrer or referrer.is_blocked:
        return

    user.referral_counted = True
    await session.execute(
        update(User)
        .where(User.user_id == referrer.user_id)
        .values(referrals_count=User.referrals_count + 1)
    )
    await session.commit()

    username_display = (
        f"@{escape(user.username)}"
        if user.username
        else escape(user.first_name or str(user.user_id))
    )
    if bot:
        try:
            accepted_reason = (
                "прошёл проверку номера"
                if phone_check_enabled
                else "прошёл обязательную проверку"
            )
            await bot.send_message(
                referrer.user_id,
                f"✅ Реферал {username_display} {accepted_reason} и теперь засчитан. "
                "Награда будет начислена после выполнения остальных условий.",
            )
        except Exception as e:
            logger.warning("Failed to notify referrer %s of accepted phone: %s", referrer.user_id, e)


async def notify_referrer_phone_rejected(user: User, session: AsyncSession, bot: Bot | None = None) -> None:
    """Notify the inviter once when a referred user is outside the allowlist."""
    if not user.referrer_id or user.phone_rejection_notified:
        return

    referrer = await UserRepository(session).get(user.referrer_id)
    user.phone_rejection_notified = True
    await session.commit()
    if not referrer or not bot:
        return

    username_display = (
        f"@{escape(user.username)}"
        if user.username
        else escape(user.first_name or str(user.user_id))
    )
    try:
        await bot.send_message(
            referrer.user_id,
            f"ℹ️ Реферал {username_display} не засчитан: его номер не относится к "
            "разрешённым странам. Если он отправит подходящий номер, проверка будет пройдена повторно.",
        )
    except Exception as e:
        logger.warning("Failed to notify referrer %s of rejected phone: %s", referrer.user_id, e)


async def notify_user_sponsors_verified(user: User, session: AsyncSession, bot: Bot) -> None:
    """Tell the referred user they've passed sponsors and how many tasks remain."""
    if not user.referrer_id or user.referral_reward_given:
        return
    repo = SettingsRepository(session)
    min_tasks = await repo.get_int("min_tasks_for_referral", 3)
    remaining = max(0, min_tasks - user.tasks_completed_count)
    if remaining <= 0:
        return
    if remaining == 1:
        word = "задание"
    elif remaining in (2, 3, 4):
        word = "задания"
    else:
        word = "заданий"
    try:
        await bot.send_message(
            user.user_id,
            f"✅ <b>Вы подписались на спонсоров!</b>\n\n"
            f"Осталось выполнить ещё <b>{remaining} {word}</b> для активации реферальной программы.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Failed to notify user %s sponsors passed: %s", user.user_id, e)


async def notify_referrer_sponsors_verified(user: User, session: AsyncSession, bot: Bot) -> None:
    """Notify referrer that their referred user passed the sponsor wall."""
    if not user.referrer_id or user.referral_reward_given:
        return
    repo = SettingsRepository(session)
    min_tasks = await repo.get_int("min_tasks_for_referral", 3)
    remaining = max(0, min_tasks - user.tasks_completed_count)
    if remaining <= 0:
        return
    username_display = (
        f"@{escape(user.username)}"
        if user.username
        else escape(user.first_name or str(user.user_id))
    )
    word = "задание" if remaining == 1 else "задания" if remaining in (2, 3, 4) else "заданий"
    try:
        await bot.send_message(
            user.referrer_id,
            f"✅ <b>{username_display} подписался на спонсоров!</b>\n\n"
            f"Осталось выполнить <b>{remaining} {word}</b> для получения награды.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Failed to notify referrer %s sponsors passed: %s", user.referrer_id, e)


async def notify_referrer_joined(referrer_id: int, new_user: User, session: AsyncSession, bot: Bot) -> None:
    """Notify referrer that someone joined via their link."""
    settings = get_settings()
    repo = SettingsRepository(session)
    reward = await get_referral_reward(session)
    min_tasks = await repo.get_int("min_tasks_for_referral", 3)
    username_display = (
        f"@{escape(new_user.username)}"
        if new_user.username
        else escape(new_user.first_name or str(new_user.user_id))
    )

    conditions: list[str] = []
    if settings.tgrass_code or settings.botohub_key:
        conditions.append("• подпишется на всех спонсоров;")
    conditions.append(f"• выполнит минимум <b>{min_tasks}</b> заданий.")

    try:
        await bot.send_message(
            referrer_id,
            f"⚡ Пользователь {username_display} присоединился по вашей ссылке!\n\n"
            f"Вы получите <b>{format_stars(reward)} ⭐</b>, когда он:\n"
            + "\n".join(conditions),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Failed to notify referrer %s of new join: %s", referrer_id, e)
