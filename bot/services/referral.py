import json
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
        await session.execute(
            update(User)
            .where(User.user_id == requested_referrer_id)
            .values(stars_balance=User.stars_balance + reward)
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return None

    if bot:
        username_display = (
            f"@{escape(user.username)}"
            if user.username
            else escape(user.first_name or str(user.user_id))
        )
        try:
            await bot.send_message(
                requested_referrer_id,
                f"♻️ Ваш реферал {username_display} вернулся по ссылке после {REFERRAL_RETURN_DAYS} дней неактивности.\n\nНачислено <b>{format_stars(reward)} ⭐</b> — половина обычной награды.",
                parse_mode="HTML",
            )
        except:
            pass
    return reward


async def check_referral_reward(user: User, session: AsyncSession, bot: Bot | None = None) -> None:
    """Check if this user has fulfilled conditions → pay referral reward to referrer once."""
    if user.referral_reward_given:
        return
    if not user.referrer_id:
        return

    if not user.sponsors_verified:
        return

    tg_count = 0
    web_count = 0
    try:
        if user.sponsor_wave_one:
            wave_one = json.loads(user.sponsor_wave_one)
            for s in wave_one:
                url = s.get("url", "")
                if "t.me/" in url or "telegram.me/" in url or "telegram.dog/" in url:
                    tg_count += 1
                else:
                    web_count += 1
        if user.sponsor_wave_two:
            wave_two = json.loads(user.sponsor_wave_two)
            for s in wave_two:
                url = s.get("url", "")
                if "t.me/" in url or "telegram.me/" in url or "telegram.dog/" in url:
                    tg_count += 1
                else:
                    web_count += 1
    except Exception as e:
        logger.error(f"Failed to parse sponsor waves: {e}")
        return

    total_sponsors = tg_count + web_count
    
    if total_sponsors < 6:
        user.referral_reward_given = True
        await session.commit()
        if bot:
            try:
                await bot.send_message(
                    user.referrer_id,
                    f"⚠️ Вашему рефералу было предоставлено недостаточно спонсоров для начисления реферальной награды (минимум 6, было {total_sponsors})."
                )
            except:
                pass
        return

    reward = Decimal(str(tg_count * 0.5 + web_count * 0.25))

    user_repo = UserRepository(session)
    referrer = await user_repo.get(user.referrer_id)
    if not referrer:
        return

    user.referral_reward_given = True
    new_referrals_count = referrer.referrals_count + 1
    
    # Explicitly pull is_vip as boolean from DB (fallback to False)
    referrer_is_vip = getattr(referrer, 'is_vip', False)

    bonus = Decimal("0")
    became_vip = False

    if new_referrals_count == 10:
        bonus += Decimal("0.1")
    elif new_referrals_count == 25:
        bonus += Decimal("0.3")
    elif new_referrals_count == 30:
        bonus += Decimal("0.4")
    elif new_referrals_count == 50:
        bonus += Decimal("0.7")
        referrer_is_vip = True
        became_vip = True
    elif new_referrals_count == 55:
        bonus += Decimal("0.8")
    elif new_referrals_count == 60:
        bonus += Decimal("0.9")
    elif new_referrals_count == 70:
        bonus += Decimal("1.0")
    elif new_referrals_count > 70:
        bonus += Decimal("1.0")

    total_reward = reward + bonus

    await session.execute(
        update(User)
        .where(User.user_id == referrer.user_id)
        .values(
            stars_balance=User.stars_balance + total_reward,
            referrals_count=new_referrals_count,
            is_vip=referrer_is_vip
        )
    )
    await session.commit()

    if bot:
        username_display = (
            f"@{escape(user.username)}"
            if user.username
            else escape(user.first_name or str(user.user_id))
        )
        try:
            msg = f"🎉 Вам начислено <b>{format_stars(total_reward)} ⭐</b> за пользователя {username_display}.\n"
            msg += f"(ТГ спонсоров: {tg_count}, Web спонсоров: {web_count})"
            if bonus > 0:
                msg += f"\n🎁 Бонус за достижение: +{format_stars(bonus)} ⭐!"
            if became_vip:
                msg += "\n🌟 Вы получили VIP-статус! При достижении 70 рефералов вы начнете получать бонус +1 ⭐ за каждого следующего!"
            elif new_referrals_count > 70:
                msg += "\n🌟 Включен VIP-бонус +1 ⭐!"
                
            await bot.send_message(
                referrer.user_id,
                msg,
                parse_mode="HTML",
            )
        except:
            pass


async def mark_referral_phone_accepted(
    user: User,
    session: AsyncSession,
    bot: Bot | None = None,
    *,
    phone_check_enabled: bool = True,
) -> None:
    pass

async def notify_referrer_phone_rejected(user: User, session: AsyncSession, bot: Bot | None = None) -> None:
    pass


async def notify_user_sponsors_verified(user: User, session: AsyncSession, bot: Bot) -> None:
    if not user.referrer_id or user.referral_reward_given:
        return
    try:
        await bot.send_message(
            user.user_id,
            f"✅ <b>Вы подписались на спонсоров!</b>",
            parse_mode="HTML",
        )
    except:
        pass


async def notify_referrer_sponsors_verified(user: User, session: AsyncSession, bot: Bot) -> None:
    pass


async def notify_referrer_joined(referrer_id: int, new_user: User, session: AsyncSession, bot: Bot) -> None:
    username_display = (
        f"@{escape(new_user.username)}"
        if new_user.username
        else escape(new_user.first_name or str(new_user.user_id))
    )
    try:
        await bot.send_message(
            referrer_id,
            f"⚡ Пользователь {username_display} присоединился по вашей ссылке!\n\nВы получите награду когда он подпишется на всех спонсоров.",
            parse_mode="HTML",
        )
    except:
        pass
