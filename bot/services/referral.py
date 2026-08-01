import asyncio
import json
import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import escape

from aiogram import Bot
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ReferralReactivation, User
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository
from bot.services.sponsor_waves import classify_sponsor_type
from bot.services.telegram_chat import is_subscribed, telegram_chat_id

logger = logging.getLogger(__name__)

REFERRAL_RETURN_DAYS = 7
_STAR_STEP = Decimal("0.01")

# Referral-count thresholds (in ascending order) mapped to the settings key
# holding that milestone's one-time bonus amount. Counts above the highest
# threshold keep earning that same top-tier bonus for every new referral.
MILESTONE_SETTINGS: list[tuple[int, str]] = [
    (10, "referral_bonus_10"),
    (25, "referral_bonus_25"),
    (30, "referral_bonus_30"),
    (50, "referral_bonus_50"),
    (55, "referral_bonus_55"),
    (60, "referral_bonus_60"),
    (70, "referral_bonus_70"),
]
VIP_THRESHOLD = 50


def format_stars(value: Decimal | float) -> str:
    amount = Decimal(str(value)).quantize(_STAR_STEP, rounding=ROUND_HALF_UP)
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def pluralize_ru(n: int, one: str, few: str, many: str) -> str:
    """Russian noun agreement: 1 спонсор / 2 спонсора / 5 спонсоров."""
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        return many
    last_digit = n_abs % 10
    if last_digit == 1:
        return one
    if 2 <= last_digit <= 4:
        return few
    return many


def sponsors_word(n: int) -> str:
    return pluralize_ru(n, "спонсор", "спонсора", "спонсоров")


def referrals_word(n: int) -> str:
    return pluralize_ru(n, "реферал", "реферала", "рефералов")


async def _get_decimal_setting(session: AsyncSession, key: str, default: str) -> Decimal:
    raw = await SettingsRepository(session).get(key, default)
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError):
        value = Decimal(default)
    return max(Decimal("0"), value).quantize(_STAR_STEP, rounding=ROUND_HALF_UP)


async def get_tg_reward(session: AsyncSession) -> Decimal:
    return await _get_decimal_setting(session, "tg_sponsor_reward", "0.5")


async def get_web_reward(session: AsyncSession) -> Decimal:
    return await _get_decimal_setting(session, "web_sponsor_reward", "0.25")


async def get_min_sponsors_for_reward(session: AsyncSession) -> int:
    return await SettingsRepository(session).get_int("min_sponsors_for_reward", 6)


async def get_milestone_bonus(session: AsyncSession, new_referrals_count: int) -> Decimal:
    """One-time bonus for the exact milestone, or the top-tier bonus for every
    referral once the referrer has passed the highest configured milestone."""
    for threshold, key in MILESTONE_SETTINGS:
        if new_referrals_count == threshold:
            return await _get_decimal_setting(session, key, "0")
    if new_referrals_count > MILESTONE_SETTINGS[-1][0]:
        top_key = MILESTONE_SETTINGS[-1][1]
        return await _get_decimal_setting(session, top_key, "0")
    return Decimal("0")


def _current_sponsor_urls(user: User) -> tuple[list[str], list[str]]:
    """Telegram-resource vs web/other sponsor URLs from the frozen waves."""
    tg_urls: list[str] = []
    web_urls: list[str] = []
    for raw_wave in (user.sponsor_wave_one, user.sponsor_wave_two):
        if not raw_wave:
            continue
        try:
            items = json.loads(raw_wave)
        except (TypeError, ValueError):
            continue
        for item in items:
            url = item.get("url", "") if isinstance(item, dict) else ""
            if not url:
                continue
            (tg_urls if classify_sponsor_type(url) == "tg" else web_urls).append(url)
    return tg_urls, web_urls


async def _verify_tg_subscriptions(bot: Bot, user_id: int, urls: list[str]) -> list[str]:
    """Independently confirm TG sponsor subscriptions via the bot's own Bot
    API, instead of trusting a possibly stale/incorrect provider report.
    Unconfirmable URLs (unresolvable chat, API error, not actually a member)
    are simply dropped from this cycle — they are not marked as rewarded, so
    they get re-checked on the next cycle rather than lost forever."""

    async def _check(url: str) -> str | None:
        chat_id = telegram_chat_id(url)
        if chat_id is None:
            return None
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            return None
        return url if is_subscribed(member) else None

    results = await asyncio.gather(*(_check(url) for url in urls), return_exceptions=True)
    return [url for url in results if isinstance(url, str)]


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

    tg_reward = await get_tg_reward(session)
    web_reward = await get_web_reward(session)

    referred_user_id = user.user_id
    tg_urls, web_urls = _current_sponsor_urls(user)
    tg_count, web_count = len(tg_urls), len(web_urls)

    total_base_reward = (Decimal(str(tg_count)) * tg_reward) + (Decimal(str(web_count)) * web_reward)
    reward = (total_base_reward / Decimal("2")).quantize(_STAR_STEP, rounding=ROUND_HALF_UP)
    
    if reward <= 0:
        return None

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
    """Pay the referrer for every sponsor the referred user has newly
    subscribed to since the last payout — not just once. Sponsors that were
    already paid for are tracked in `rewarded_sponsor_urls` and never billed
    twice. Milestone bonuses / VIP / referrals_count only ever increment
    once per referred user, on their first qualifying payout; every later
    cycle only pays the per-sponsor reward for whatever is new."""
    if not user.referrer_id:
        return
    if not user.sponsors_verified:
        return

    tg_urls, web_urls = _current_sponsor_urls(user)
    rewarded = set(json.loads(user.rewarded_sponsor_urls) if user.rewarded_sponsor_urls else [])
    new_tg = [u for u in tg_urls if u not in rewarded]
    new_web = [u for u in web_urls if u not in rewarded]

    if bot and new_tg:
        # Don't trust the provider's "subscribed" report blindly — confirm
        # each new TG sponsor ourselves before paying for it. Web sponsors
        # are redirects/webapps we have no independent way to check, so
        # those keep relying on the provider as before.
        new_tg = await _verify_tg_subscriptions(bot, user.user_id, new_tg)

    new_total = len(new_tg) + len(new_web)
    if new_total == 0:
        return

    min_sponsors = await get_min_sponsors_for_reward(session)
    if new_total < min_sponsors:
        if not user.referral_insufficient_notified:
            user.referral_insufficient_notified = True
            await session.commit()
            if bot:
                try:
                    await bot.send_message(
                        user.referrer_id,
                        f"⚠️ Вашему рефералу было предоставлено недостаточно спонсоров для начисления реферальной награды (минимум {min_sponsors}, было {new_total})."
                    )
                except Exception:
                    pass
        return

    tg_reward = await get_tg_reward(session)
    web_reward = await get_web_reward(session)
    reward = (Decimal(str(len(new_tg))) * tg_reward) + (Decimal(str(len(new_web))) * web_reward)

    user_repo = UserRepository(session)
    referrer = await user_repo.get(user.referrer_id)
    if not referrer:
        return

    rewarded |= set(new_tg) | set(new_web)
    user.rewarded_sponsor_urls = json.dumps(sorted(rewarded))
    user.referral_counted = True

    first_time = not user.referral_reward_given
    user.referral_reward_given = True

    bonus = Decimal("0")
    became_vip = False
    new_referrals_count = referrer.referrals_count
    referrer_is_vip = getattr(referrer, "is_vip", False)

    if first_time:
        new_referrals_count = referrer.referrals_count + 1
        became_vip = new_referrals_count >= VIP_THRESHOLD and not referrer_is_vip
        if became_vip:
            referrer_is_vip = True
        bonus = await get_milestone_bonus(session, new_referrals_count)

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
            if first_time:
                msg = f"🎉 Вам начислено <b>{format_stars(total_reward)} ⭐</b> за пользователя {username_display}.\n"
                msg += f"(ТГ спонсоров: {len(new_tg)}, Web спонсоров: {len(new_web)})"
                if bonus > 0:
                    msg += f"\n🎁 Бонус за достижение: +{format_stars(bonus)} ⭐!"
                if became_vip:
                    top_tier = MILESTONE_SETTINGS[-1][0]
                    msg += (
                        f"\n🌟 Вы получили VIP-статус! При достижении {top_tier} рефералов "
                        "вы начнете получать бонус +1 ⭐ за каждого следующего!"
                    )
                elif new_referrals_count > MILESTONE_SETTINGS[-1][0]:
                    msg += "\n🌟 Включен VIP-бонус +1 ⭐!"
            else:
                msg = (
                    f"🎉 Ваш реферал {username_display} подписался ещё на "
                    f"{new_total} {sponsors_word(new_total)} — начислено "
                    f"<b>{format_stars(total_reward)} ⭐</b>.\n"
                    f"(ТГ спонсоров: {len(new_tg)}, Web спонсоров: {len(new_web)})"
                )

            await bot.send_message(
                referrer.user_id,
                msg,
                parse_mode="HTML",
            )
        except:
            pass


async def notify_user_sponsors_verified(user: User, session: AsyncSession, bot: Bot) -> None:
    if not user.referrer_id or user.referral_reward_given:
        return
    try:
        await bot.send_message(
            user.user_id,
            "✅ <b>Вы подписались на спонсоров!</b>",
            parse_mode="HTML",
        )
    except:
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
