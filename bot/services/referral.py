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
from bot.services.sponsor_waves import classify_sponsor_type, total_sponsor_count, wave_in_progress
from bot.services.telegram_chat import is_subscribed, telegram_chat_id

logger = logging.getLogger(__name__)

REFERRAL_RETURN_DAYS = 7
_STAR_STEP = Decimal("0.01")

# Referral-count thresholds (in ascending order) mapped to the settings key
# holding that milestone's one-time bonus amount.
MILESTONE_SETTINGS: list[tuple[int, str]] = [
    (10, "referral_bonus_10"),
    (20, "referral_bonus_20"),
    (30, "referral_bonus_30"),
    (40, "referral_bonus_40"),
    (50, "referral_bonus_50"),
    (65, "referral_bonus_65"),
    (80, "referral_bonus_80"),
    (90, "referral_bonus_90"),
    (100, "referral_bonus_100"),
    (120, "referral_bonus_120"),
    (140, "referral_bonus_140"),
    (145, "referral_bonus_145"),
    (150, "referral_bonus_150"),
    (155, "referral_bonus_155"),
    (170, "referral_bonus_170"),
    (190, "referral_bonus_190"),
    (200, "referral_bonus_200"),
    (250, "referral_bonus_250"),
    (350, "referral_bonus_350"),
    (450, "referral_bonus_450"),
]

# From each referral count onward, every NEW referral additionally earns
# this per-referral bonus on top of the base reward (and any one-time
# milestone above) — the rate REPLACES the previous tier's rate rather than
# stacking with it. Checked highest-first so the highest tier reached wins.
RECURRING_TIERS: list[tuple[int, str]] = [
    (500, "referral_recurring_500"),
    (400, "referral_recurring_400"),
    (300, "referral_recurring_300"),
    (200, "referral_recurring_200"),
]

# Purely cosmetic statuses once reached — no bonus, no message here; they
# only surface as badges on the user's withdrawal requests / /info (see
# withdraw.py etc.). Checked highest-first so the highest tier reached wins;
# independent of VIP, which stays its own separate badge.
VIP_THRESHOLD = 50
PREMIUM_THRESHOLD = 200
SIGMA_THRESHOLD = 300
GOOD_THRESHOLD = 500
REFERRAL_TIERS: list[tuple[int, str]] = [
    (GOOD_THRESHOLD, "good"),
    (SIGMA_THRESHOLD, "sigma"),
    (PREMIUM_THRESHOLD, "premium"),
]
REFERRAL_TIER_BADGES: dict[str, str] = {
    "premium": " 👑 Premium",
    "sigma": " 🐺 Sigma",
    "good": " 🌟 Good",
}


def referral_tier_for_count(count: int) -> str | None:
    for threshold, tier in REFERRAL_TIERS:
        if count >= threshold:
            return tier
    return None


def referral_tier_badge(tier: str | None) -> str:
    return REFERRAL_TIER_BADGES.get(tier or "", "")


def vip_and_tier_badge(is_vip: bool, tier: str | None) -> str:
    """Cosmetic badge suffix for withdrawal requests / admin posts -- shows
    only the single HIGHEST title reached (VIP(50) < Premium(200) <
    Sigma(300) < Good(500)), never stacks multiple badges. Single source
    of truth so every call site stays in sync."""
    tier_badge = referral_tier_badge(tier)
    if tier_badge:
        return tier_badge
    return " 💎 VIP" if is_vip else ""


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


REFERRAL_REWARD_UPPER_TIER_THRESHOLD = 5
REFERRAL_REWARD_TOP_TIER_THRESHOLD = 8


async def get_referral_reward(session: AsyncSession, sponsor_count: int, is_premium: bool = False) -> Decimal:
    """Premium referrals pay one flat, separately configurable amount
    regardless of sponsor count. Regular referrals pay a flat base reward
    for 3-5 sponsors, a higher flat reward for 6-8, and a top flat reward
    for 9+. The min-sponsors-for-reward gate (see
    get_min_sponsors_for_reward) still applies to all three before any of
    them is even reached."""
    if is_premium:
        return await _get_decimal_setting(session, "referral_reward_premium", "13.5")
    if sponsor_count > REFERRAL_REWARD_TOP_TIER_THRESHOLD:
        return await _get_decimal_setting(session, "referral_reward_top", "13.5")
    if sponsor_count > REFERRAL_REWARD_UPPER_TIER_THRESHOLD:
        return await _get_decimal_setting(session, "referral_reward_above_5", "10.5")
    return await _get_decimal_setting(session, "referral_reward", "9")


async def get_min_sponsors_for_reward(session: AsyncSession) -> int:
    return await SettingsRepository(session).get_int("min_sponsors_for_reward", 3)


async def get_milestone_bonus(session: AsyncSession, new_referrals_count: int) -> Decimal:
    """One-time bonus for the exact milestone (if any) PLUS the recurring
    per-referral bonus for the highest tier reached (if any) — both can
    apply to the same referral, e.g. reaching exactly 200 pays the
    200-milestone bonus AND the newly-unlocked 200+ recurring rate for that
    same referral."""
    total = Decimal("0")
    for threshold, key in MILESTONE_SETTINGS:
        if new_referrals_count == threshold:
            total += await _get_decimal_setting(session, key, "0")
            break
    for threshold, key in RECURRING_TIERS:
        if new_referrals_count >= threshold:
            total += await _get_decimal_setting(session, key, "0")
            break
    return total


async def get_recurring_tier_rate(session: AsyncSession, threshold: int) -> Decimal:
    """The flat per-referral rate for one specific recurring-tier threshold
    (200/300/400/500) — for display purposes (see earn.py). Unlike
    get_milestone_bonus, this never adds an overlapping one-time milestone
    bonus on top."""
    for t, key in RECURRING_TIERS:
        if t == threshold:
            return await _get_decimal_setting(session, key, "0")
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
    Most sponsor channels come from ad networks (Traffy/BotoHub/tgrass/
    FlyerHub) our bot was never added to, and some use private invite links
    that never resolve to a chat id — for those we simply CAN'T verify
    independently, so an unresolvable/failed check falls back to trusting
    the provider (which already confirmed the subscription before the wall
    let the user through) rather than silently dropping the sponsor. Only a
    successful lookup that positively shows the user isn't a member
    (left/kicked/banned) excludes the URL."""

    async def _check(url: str) -> str | None:
        chat_id = telegram_chat_id(url)
        if chat_id is None:
            return url
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            return url
        return url if is_subscribed(member) else None

    results = await asyncio.gather(*(_check(url) for url in urls), return_exceptions=True)
    return [url for url in results if isinstance(url, str)]


def _reactivation_eligible(
    user: User, requested_referrer_id: int, previous_last_seen_at: datetime | None,
) -> bool:
    """Shared guard for both paying a reactivation reward and deciding
    whether to even trigger a fresh sponsor wall for one: a genuinely
    stale, previously-completed referral relationship, inactive for at
    least REFERRAL_RETURN_DAYS."""
    if (
        previous_last_seen_at is None
        or user.referrer_id != requested_referrer_id
        or not user.referral_counted
        or not user.referral_reward_given
    ):
        return False
    return previous_last_seen_at <= datetime.utcnow() - timedelta(days=REFERRAL_RETURN_DAYS)


async def trigger_referral_reactivation_wall(
    user: User,
    requested_referrer_id: int,
    previous_last_seen_at: datetime | None,
    session: AsyncSession,
) -> bool:
    """Re-arms the exact same mandatory sponsor wall a brand-new signup
    goes through (SponsorWallMiddleware only short-circuits on
    sponsors_verified=True; initialize_waves only skips re-freezing when
    sponsor_wave is already 1/2) for a referral returning via their
    original link after REFERRAL_RETURN_DAYS of inactivity. The actual
    reactivation reward is paid later, once that fresh wall clears -- see
    resolve_pending_reactivation. Returns True if a wall was armed."""
    if not _reactivation_eligible(user, requested_referrer_id, previous_last_seen_at):
        return False
    if wave_in_progress(user) or user.pending_reactivation_referrer_id is not None:
        # A wave is genuinely frozen mid-progress right now (don't yank it
        # out from under them), or a reactivation is already in flight
        # (don't clobber it with a fresher previous_last_seen_at).
        return False

    user.sponsor_wave = 0
    user.sponsor_wave_one = None
    user.sponsor_wave_two = None
    user.sponsors_verified = False
    user.pending_reactivation_referrer_id = requested_referrer_id
    user.pending_reactivation_since = previous_last_seen_at
    await session.commit()
    return True


async def resolve_pending_reactivation(user: User, session: AsyncSession, bot: Bot | None = None) -> None:
    """Called wherever sponsors_verified just transitioned to True -- pays
    out the reactivation reward (if any is pending) using the freshly
    cleared sponsor wave instead of the normal check_referral_reward path
    (a no-op here anyway, since referral_reward_given is already True for
    a returning referral)."""
    referrer_id = user.pending_reactivation_referrer_id
    if referrer_id is None:
        return
    since = user.pending_reactivation_since
    # Cleared only AFTER a successful call, not before: if this raises (a
    # transient DB error -- reward_returning_referral already swallows its
    # own expected IntegrityError internally), the pending markers are left
    # exactly as they were, so the next genuine sponsors_verified
    # transition retries this reactivation instead of silently losing it.
    await reward_returning_referral(user, referrer_id, since, session, bot)
    user.pending_reactivation_referrer_id = None
    user.pending_reactivation_since = None
    await session.commit()


async def reward_returning_referral(
    user: User,
    requested_referrer_id: int,
    previous_last_seen_at: datetime | None,
    session: AsyncSession,
    bot: Bot | None = None,
) -> Decimal | None:
    if not _reactivation_eligible(user, requested_referrer_id, previous_last_seen_at):
        return None

    now = datetime.utcnow()
    referrer = await UserRepository(session).get(requested_referrer_id)
    if not referrer or referrer.is_blocked:
        return None

    referred_user_id = user.user_id
    # Only ever called via resolve_pending_reactivation, after
    # trigger_referral_reactivation_wall has already forced the user
    # through a brand-new sponsor wall -- sponsor_wave_one/two here is the
    # FRESH wave they just cleared, not the original signup one.
    tg_urls, web_urls = _current_sponsor_urls(user)
    if bot and tg_urls:
        # Re-verify independently rather than trusting the wall's own pass
        # blindly, same don't-trust-the-provider principle as
        # check_referral_reward.
        tg_urls = await _verify_tg_subscriptions(bot, referred_user_id, tg_urls)
    sponsor_count = len(tg_urls) + len(web_urls)

    min_sponsors = await get_min_sponsors_for_reward(session)
    if sponsor_count < min_sponsors:
        # Unlike check_referral_reward's insufficient-sponsors branch, this
        # has no persisted "already notified" flag to dedupe repeats -- not
        # needed here, since resolve_pending_reactivation only clears the
        # pending columns AFTER this call returns, so a failed/incomplete
        # attempt is retried wholesale (including this notification) rather
        # than silently resumed partway through.
        if bot:
            try:
                await bot.send_message(
                    user.user_id,
                    f"⚠️ Подписался(-ась) на недостаточное количество спонсоров "
                    f"({sponsor_count} из {min_sponsors}) — награда твоему другу "
                    f"за это возвращение не начислена.",
                )
            except Exception:
                pass
            try:
                await bot.send_message(
                    requested_referrer_id,
                    f"⚠️ Вернувшийся по вашей ссылке реферал подписался на "
                    f"недостаточное количество спонсоров — повторная награда не начислена.",
                )
            except Exception:
                pass
        return None

    base_reward = await get_referral_reward(session, sponsor_count, is_premium=bool(user.is_premium))
    reward = (base_reward / Decimal("2")).quantize(_STAR_STEP, rounding=ROUND_HALF_UP)

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
                f"♻️ Ваш реферал {username_display} вернулся по ссылке после {REFERRAL_RETURN_DAYS} дней неактивности.\n\nНачислено <b>{format_stars(reward)} RP⭐️</b> — половина обычной награды.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    return reward


async def check_referral_reward(user: User, session: AsyncSession, bot: Bot | None = None) -> None:
    """Pay the referrer exactly once per referred user. All of the referred
    user's TG sponsor subscriptions are independently confirmed via the
    bot's own Bot API before anything is counted, so an unconfirmed sponsor
    never contributes to the payout. If, after verification, the confirmed
    total is below the minimum, no payment is made (and the referrer is
    notified once). Once `referral_reward_given` is set, this is a no-op
    forever for that referred user — new sponsors gained later are never
    paid for again."""
    if not user.referrer_id:
        return
    if not user.sponsors_verified:
        return
    if user.referral_reward_given:
        return

    # Captured once, up front: session.rollback() (used below by the
    # referrals_count conflict retry) expires every attribute on every
    # object in the session, including `user` — re-reading user.xxx after
    # that would trigger an implicit lazy load outside of any awaited
    # call, which raises MissingGreenlet under SQLAlchemy's async engine.
    referred_user_id = user.user_id
    referrer_id = user.referrer_id
    referred_username = user.username
    referred_first_name = user.first_name
    referred_is_premium = bool(user.is_premium)

    tg_urls, web_urls = _current_sponsor_urls(user)
    tg_pre = len(tg_urls)
    web_count = len(web_urls)

    if bot and tg_urls:
        # Don't trust the provider's "subscribed" report blindly — confirm
        # each TG sponsor ourselves before counting it. Web sponsors are
        # redirects/webapps we have no independent way to check, so those
        # keep relying on the provider as before.
        tg_urls = await _verify_tg_subscriptions(bot, referred_user_id, tg_urls)
    tg_post = len(tg_urls)

    total = len(tg_urls) + len(web_urls)

    min_sponsors = await get_min_sponsors_for_reward(session)
    if total < min_sponsors:
        logger.info(
            "REFERRAL outcome=insufficient referred_uid=%s referrer_uid=%s tg_pre=%d tg_post=%d web=%d total=%d min=%d",
            referred_user_id, referrer_id, tg_pre, tg_post, web_count, total, min_sponsors,
        )
        if not user.referral_insufficient_notified:
            user.referral_insufficient_notified = True
            await session.commit()
            if bot:
                try:
                    await bot.send_message(
                        referrer_id,
                        f"⚠️ Вашему рефералу было предоставлено недостаточно спонсоров для начисления реферальной награды (минимум {min_sponsors}, было {total})."
                    )
                except Exception:
                    pass
                try:
                    await bot.send_message(
                        referred_user_id,
                        f"⚠️ Твоему другу, который тебя пригласил, не начислилась награда за тебя, "
                        f"т.к. ты подписался на недостаточное количество спонсоров "
                        f"(минимум {min_sponsors}, было {total}).",
                    )
                except Exception:
                    pass
        return

    # Atomically claim this referred user's reward slot before paying the
    # referrer anything. The plain `if user.referral_reward_given: return`
    # guard at the top of this function only reflects the in-memory state
    # of the ORM object THIS call happened to load — aiogram can process
    # two updates for the same user concurrently (e.g. a double-tap on "Я
    # подписался на все каналы", one message + one callback arriving close
    # together), each with its own session/its own stale copy of `user`,
    # so both could pass that check and both pay the referrer before
    # either commits. This WHERE-guarded UPDATE mirrors the same
    # optimistic-concurrency pattern used below for referrer.referrals_count
    # — only one concurrent call can ever flip referral_reward_given from
    # False to True, so only one can proceed past this point.
    #
    # Committed immediately (not left pending alongside the referrer-payment
    # retry loop below): that loop's own conflict-recovery rollback() would
    # otherwise silently undo this claim mid-loop, reopening a window where
    # a second concurrent call could win its own claim and pay the referrer
    # again while this call's retry loop is still in flight — the exact
    # double payment this claim exists to prevent, just shifted one step
    # later. Every early-return after this point must explicitly undo the
    # claim (see _unclaim below) so a referrer who never actually got paid
    # doesn't leave the referred user permanently stuck "rewarded".
    claim = await session.execute(
        update(User)
        .where(User.user_id == referred_user_id, User.referral_reward_given.is_(False))
        .values(referral_reward_given=True)
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        await session.rollback()
        return
    await session.commit()

    async def _unclaim() -> None:
        await session.execute(
            update(User)
            .where(User.user_id == referred_user_id)
            .values(referral_reward_given=False)
            .execution_options(synchronize_session=False)
        )
        await session.commit()

    user_repo = UserRepository(session)
    referrer = await user_repo.get(referrer_id)
    if not referrer:
        await _unclaim()
        return

    reward = await get_referral_reward(session, total, is_premium=referred_is_premium)

    # referrals_count is read-then-written, so two referrals for the same
    # referrer completing at nearly the same moment (plausible with the
    # bulk/bot signups this system has to defend against) could otherwise
    # race and silently lose an increment — or worse, miscompute the
    # milestone bonus off a stale count. Guard the update with the snapshot
    # referrals_count and retry on conflict, mirroring the same
    # optimistic-concurrency pattern already used for duels/lottery/auction.
    for _ in range(5):
        new_referrals_count = referrer.referrals_count + 1
        # VIP is a cosmetic badge only — it doesn't affect the reward math or
        # trigger any message here; it only shows up on the user's own
        # withdrawal requests (see withdraw.py).
        referrer_is_vip = getattr(referrer, "is_vip", False) or new_referrals_count >= VIP_THRESHOLD
        # Sticky-or-computed, same as is_vip above: never downgrade a tier
        # the referrer already reached (referrals_count only grows, so this
        # is defensive rather than load-bearing).
        referrer_tier = referral_tier_for_count(new_referrals_count) or getattr(referrer, "referral_tier", None)
        bonus = await get_milestone_bonus(session, new_referrals_count)
        total_reward = reward + bonus

        claim = await session.execute(
            update(User)
            .where(
                User.user_id == referrer.user_id,
                User.referrals_count == referrer.referrals_count,
            )
            .values(
                stars_balance=User.stars_balance + total_reward,
                referrals_count=new_referrals_count,
                is_vip=referrer_is_vip,
                referral_tier=referrer_tier,
            )
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount == 1:
            break
        await session.rollback()
        referrer = await user_repo.get(referrer_id)
        if not referrer:
            await _unclaim()
            return
    else:
        logger.warning(
            "REFERRAL outcome=conflict referred_uid=%s referrer_uid=%s — gave up after retries",
            referred_user_id, referrer_id,
        )
        await _unclaim()
        return

    user.referral_counted = True
    user.referral_reward_given = True
    await session.commit()
    logger.info(
        "REFERRAL outcome=paid referred_uid=%s referrer_uid=%s tg_pre=%d tg_post=%d web=%d total=%d min=%d reward=%s bonus=%s is_vip=%s tier=%s is_premium=%s",
        referred_user_id, referrer_id, tg_pre, tg_post, web_count, total, min_sponsors,
        reward, bonus, referrer_is_vip, referrer_tier, referred_is_premium,
    )

    if bot:
        username_display = (
            f"@{escape(referred_username)}"
            if referred_username
            else escape(referred_first_name or str(referred_user_id))
        )
        premium_tag = " 💎" if referred_is_premium else ""
        try:
            msg = f"🎉 Вам начислено <b>{format_stars(total_reward)} RP⭐️</b> за пользователя {username_display}{premium_tag}."
            if bonus > 0:
                msg += f"\n🎁 Бонус за достижение: +{format_stars(bonus)} RP⭐️!"

            await bot.send_message(
                referrer.user_id,
                msg,
                parse_mode="HTML",
            )
        except Exception:
            pass


async def notify_user_sponsors_verified(user: User, session: AsyncSession, bot: Bot) -> None:
    if not user.referrer_id or user.referral_reward_given:
        return
    count = total_sponsor_count(user)
    try:
        await bot.send_message(
            user.user_id,
            f"✅ <b>Вы подписались на {count} {sponsors_word(count)}!</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


async def notify_referrer_joined(referrer_id: int, new_user: User, session: AsyncSession, bot: Bot) -> None:
    username_display = (
        f"@{escape(new_user.username)}"
        if new_user.username
        else escape(new_user.first_name or str(new_user.user_id))
    )
    premium_tag = " 💎 <b>(Premium)</b>" if new_user.is_premium else ""
    try:
        await bot.send_message(
            referrer_id,
            f"⚡ Пользователь {username_display}{premium_tag} присоединился по вашей ссылке!\n\nВы получите награду когда он подпишется на всех спонсоров.",
            parse_mode="HTML",
        )
    except Exception:
        pass
