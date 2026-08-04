from datetime import datetime, timedelta
from decimal import Decimal
from html import escape
from math import ceil

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.content import DEFAULT_TEXTS, ContentRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.earn import earn_kb, return_referrals_kb
from bot.services.referral import (
    MILESTONE_SETTINGS,
    RECURRING_MILESTONE,
    REFERRAL_RETURN_DAYS,
    VIP_THRESHOLD,
    format_stars,
    get_milestone_bonus,
    get_min_sponsors_for_reward,
    get_referral_reward,
    sponsors_word,
)

router = Router()
settings = get_settings()
RETURN_PAGE_SIZE = 8


@router.callback_query(lambda c: c.data == "menu:earn")
async def cb_earn(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    repo = ContentRepository(session)
    template = await repo.get_text("earn")
    ref_link = f"https://t.me/{settings.bot_username}?start=ref_{db_user.user_id}"
    cutoff = datetime.utcnow() - timedelta(days=REFERRAL_RETURN_DAYS)
    _, returnable_count, _ = await UserRepository(session).inactive_rewarded_referrals(
        db_user.user_id,
        cutoff,
        limit=1,
    )
    reward_3 = await get_referral_reward(session, 3)
    reward_4 = await get_referral_reward(session, 4)
    reward_5 = await get_referral_reward(session, 5)
    reward_3_premium = await get_referral_reward(session, 3, is_premium=True)
    reward_4_premium = await get_referral_reward(session, 4, is_premium=True)
    reward_5_premium = await get_referral_reward(session, 5, is_premium=True)
    min_sponsors = await get_min_sponsors_for_reward(session)
    top_tier = RECURRING_MILESTONE
    top_bonus = await get_milestone_bonus(session, top_tier)
    milestone_bonuses = {
        threshold: await get_milestone_bonus(session, threshold)
        for threshold, _ in MILESTONE_SETTINGS
    }

    format_kwargs = dict(
        referrals=db_user.referrals_count,
        link=ref_link,
        balance=float(db_user.stars_balance),
        min_sponsors=min_sponsors,
        min_sponsors_word=sponsors_word(min_sponsors),
        vip_threshold=VIP_THRESHOLD,
        top_tier=top_tier,
        top_bonus=format_stars(top_bonus),
        reward=format_stars(reward_3),  # fallback for old templates
        referral_reward_3=format_stars(reward_3),
        referral_reward_4=format_stars(reward_4),
        referral_reward_5=format_stars(reward_5),
        referral_reward_3_premium=format_stars(reward_3_premium),
        referral_reward_4_premium=format_stars(reward_4_premium),
        referral_reward_5_premium=format_stars(reward_5_premium),
        return_reward=format_stars(reward_3 / Decimal("2")),
        returnable=returnable_count,
        **{
            f"bonus_{threshold}": format_stars(bonus)
            for threshold, bonus in milestone_bonuses.items()
        },
    )

    if "{" in template:
        try:
            text = template.format(**format_kwargs)
        except (KeyError, ValueError, IndexError):
            text = DEFAULT_TEXTS["earn"].format(**format_kwargs)
    else:
        text = template

    # Some stored/admin-edited templates omit the {link} placeholder — never
    # let the referral link silently disappear from the screen.
    if ref_link not in text:
        text += (
            f"\n\n👥 Приглашено: <b>{db_user.referrals_count}</b>\n"
            f"🔗 Твоя ссылка:\n<code>{ref_link}</code>"
        )
    text += (
        f"\n\n♻️ <b>Повторные приглашения</b>\n"
        f"Если засчитанный реферал не пользуется ботом <b>{REFERRAL_RETURN_DAYS} дней</b>, "
        "он автоматически появится в списке ниже. Отправь ему свою прежнюю ссылку: "
        f"когда он вернётся по ней, ты получишь <b>половину</b> от текущей награды за его спонсоров.\n\n"
        f"Сейчас можно вернуть: <b>{returnable_count}</b>"
    )

    photo = await repo.get_photo("earn")
    kb = earn_kb(
        settings.bot_username,
        db_user.user_id,
        returnable_count,
    )

    try:
        if photo:
            await callback.message.delete()
            await callback.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(
    lambda c: c.data and c.data.startswith("earn:returns:")
)
async def cb_return_referrals(
    callback: CallbackQuery,
    db_user: User,
    session: AsyncSession,
) -> None:
    try:
        requested_page = max(0, int(callback.data.rsplit(":", 1)[1]))
    except (AttributeError, ValueError):
        requested_page = 0

    cutoff = datetime.utcnow() - timedelta(days=REFERRAL_RETURN_DAYS)
    repo = UserRepository(session)
    referrals, total, contactable = await repo.inactive_rewarded_referrals(
        db_user.user_id,
        cutoff,
        offset=requested_page * RETURN_PAGE_SIZE,
        limit=RETURN_PAGE_SIZE,
    )
    total_pages = max(1, ceil(contactable / RETURN_PAGE_SIZE))
    page = min(requested_page, total_pages - 1)
    if page != requested_page:
        referrals, total, contactable = await repo.inactive_rewarded_referrals(
            db_user.user_id,
            cutoff,
            offset=page * RETURN_PAGE_SIZE,
            limit=RETURN_PAGE_SIZE,
        )

    referral_reward = await get_referral_reward(session, 3)
    return_reward = referral_reward / 2
    without_username = max(0, total - contactable)
    if referrals:
        lines = []
        now = datetime.utcnow()
        for user in referrals:
            inactive_days = max(REFERRAL_RETURN_DAYS, (now - user.last_seen_at).days)
            display_name = escape(user.first_name or f"@{user.username}")
            lines.append(
                f"• {display_name} — <b>@{escape(user.username)}</b>, "
                f"неактивен {inactive_days} дн."
            )
        body = "\n".join(lines)
    else:
        body = (
            "Пока нет рефералов, которые не пользовались ботом "
            f"{REFERRAL_RETURN_DAYS} дней."
        )

    text = (
        "♻️ <b>Кого можно пригласить повторно</b>\n\n"
        f"{body}\n\n"
        f"Награда за возврат: <b>{return_reward:.2f} ⭐</b>\n"
        f"Доступно всего: <b>{total}</b>"
    )
    if without_username:
        text += (
            f"\nБез username (написать напрямую нельзя): <b>{without_username}</b>"
        )
    if contactable:
        text += f"\nСтраница: <b>{page + 1}/{total_pages}</b>"

    kb = return_referrals_kb(
        [user.username for user in referrals if user.username],
        page,
        total_pages,
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    await callback.answer()
