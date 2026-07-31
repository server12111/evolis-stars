from datetime import datetime, timedelta
from html import escape
from math import ceil

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.earn import earn_kb, return_referrals_kb
from bot.services.referral import (
    REFERRAL_RETURN_DAYS,
    check_referral_reward,
    format_stars,
    get_tg_reward,
    get_web_reward,
    notify_referrer_joined,
    notify_referrer_sponsors_verified,
    notify_user_sponsors_verified,
    reward_returning_referral,
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
    tg_reward = await get_tg_reward(session)
    web_reward = await get_web_reward(session)

    _default_text = (
        f"💸 <b>Заработать</b>\n\nПриглашай друзей по реферальной ссылке и получай звезды за каждого спонсора, на которого они подписались!\n\n"
        f"🔹 Спонсоры Telegram: <b>{format_stars(tg_reward)} ⭐</b>\n🔹 Web спонсоры: <b>{format_stars(web_reward)} ⭐</b>\n\n"
        f"<i>Для получения награды реферал должен подписаться на не менее 6 спонсоров.</i>\n\n"
        f"👑 <b>VIP система</b>\nПри достижении 50 рефералов вы получаете статус VIP и дополнительные +1 ⭐ за каждого следующего реферала!\n\n"
        f"👥 Приглашено: <b>{db_user.referrals_count}</b>\n🔗 Твоя ссылка:\n<code>{ref_link}</code>"
    )
    
    if template and "За одного обычного реферала:" in template:
        template = _default_text
        
    if "{" in template:
        try:
            text = template.format(
                referrals=db_user.referrals_count,
                link=ref_link,
                balance=float(db_user.stars_balance),
                reward=format_stars(tg_reward), # fallback for old templates
                referral_reward=format_stars(tg_reward), # fallback for old templates
                return_reward=format_stars(tg_reward / Decimal("2")), # fallback
                returnable=returnable_count,
            )
        except (KeyError, ValueError, IndexError):
            text = _default_text
    else:
        text = _default_text
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

    ordinary_reward = await get_referral_reward(session)
    reward = await get_return_reward(session)
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
        f"Обычная награда: <b>{format_stars(ordinary_reward)} ⭐</b>.\n"
        f"За возврат по твоей ссылке: <b>{format_stars(reward)} ⭐</b>.\n"
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
