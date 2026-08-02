import random
from datetime import datetime, timedelta

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.own_sponsor import OwnSponsorRepository
from bot.database.repositories.settings import SettingsRepository
from bot.services.telegram_chat import is_subscribed, telegram_chat_id

router = Router()

BONUS_COOLDOWN_HOURS = 24


def _own_sponsor_wall_kb(active_sponsors: list, completed_ids: set[int]) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for sponsor in active_sponsors:
        done = sponsor.id in completed_ids
        if sponsor.kind == "bot" and not done:
            # Telegram never tells us a URL button was tapped — count the
            # click via callback first, then swap to the real link below.
            builder.row(InlineKeyboardButton(text=f"➡️ {sponsor.name}", callback_data=f"own_sponsor:visit:{sponsor.id}"))
        else:
            builder.row(InlineKeyboardButton(text=f"📲 {sponsor.name}", url=sponsor.url))
    builder.row(InlineKeyboardButton(text="✅ Проверить", callback_data="own_sponsor:check"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


async def _show_own_sponsor_wall(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    repo = OwnSponsorRepository(session)
    active = await repo.all_active()
    completed_ids = await repo.completed_sponsor_ids(db_user.user_id)
    text = (
        "🎁 <b>Прежде чем забрать бонус</b>\n\n"
        "Загляни к нашим спонсорам, затем нажми «✅ Проверить»."
    )
    kb = _own_sponsor_wall_kb(active, completed_ids)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


async def _grant_daily_bonus(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    repo = SettingsRepository(session)

    enabled = await repo.get_bool("bonus_enabled", True)
    if not enabled:
        await callback.answer("🎁 Бонус временно недоступен.", show_alert=True)
        return

    now = datetime.utcnow()
    if db_user.last_bonus_at:
        next_bonus = db_user.last_bonus_at + timedelta(hours=BONUS_COOLDOWN_HOURS)
        if now < next_bonus:
            remaining = next_bonus - now
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            await callback.answer(
                f"⏳ Следующий бонус через {hours}ч {minutes}мин",
                show_alert=True,
            )
            return

    bonus_min = await repo.get_float("bonus_min", 0.1)
    bonus_max = await repo.get_float("bonus_max", 1.0)
    amount = round(random.uniform(bonus_min, bonus_max), 2)

    db_user.stars_balance = round(float(db_user.stars_balance) + amount, 2)
    db_user.last_bonus_at = now
    await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))

    text = (
        f"🎁 <b>Ежедневный бонус получен!</b>\n\n"
        f"Вам начислено: <b>+{amount:.2f} ⭐</b> (диапазон {bonus_min:.2f}–{bonus_max:.2f} ⭐)\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>\n\n"
        f"Возвращайтесь через 24 часа за новым бонусом!"
    )

    kb = builder.as_markup()
    photo = await ContentRepository(session).get_photo("bonus")
    if photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(lambda c: c.data == "menu:bonus")
async def cb_bonus(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    pending = await OwnSponsorRepository(session).pending_for_user(db_user.user_id)
    if pending:
        await _show_own_sponsor_wall(callback, db_user, session)
        await callback.answer()
        return
    await _grant_daily_bonus(callback, db_user, session)


@router.callback_query(lambda c: c.data and c.data.startswith("own_sponsor:visit:"))
async def cb_own_sponsor_visit(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    try:
        sponsor_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    repo = OwnSponsorRepository(session)
    sponsor = await repo.get(sponsor_id)
    if not sponsor or sponsor.kind != "bot":
        await callback.answer()
        return

    await repo.mark_completed(sponsor_id, db_user.user_id)

    still_pending = await repo.pending_for_user(db_user.user_id)
    if still_pending:
        # _grant_daily_bonus always answers the callback itself (alert or
        # plain) — answering here too would be a second answer on the same
        # callback query, which Telegram rejects. Only answer on this branch.
        await callback.answer()
        await _show_own_sponsor_wall(callback, db_user, session)
        return
    await _grant_daily_bonus(callback, db_user, session)


@router.callback_query(lambda c: c.data == "own_sponsor:check")
async def cb_own_sponsor_check(callback: CallbackQuery, db_user: User, session: AsyncSession, bot: Bot) -> None:
    repo = OwnSponsorRepository(session)
    pending = await repo.pending_for_user(db_user.user_id)

    for sponsor in pending:
        if sponsor.kind != "channel" or not sponsor.target:
            continue
        chat_id = telegram_chat_id(sponsor.target)
        if chat_id is None:
            continue
        try:
            member = await bot.get_chat_member(chat_id, db_user.user_id)
        except Exception:
            continue
        if is_subscribed(member):
            await repo.mark_completed(sponsor.id, db_user.user_id)

    still_pending = await repo.pending_for_user(db_user.user_id)
    if still_pending:
        await callback.answer("❌ Ты ещё не подписался на всех спонсоров.", show_alert=True)
        await _show_own_sponsor_wall(callback, db_user, session)
        return

    # _grant_daily_bonus always answers the callback itself (cooldown alert,
    # disabled alert, or plain success) — don't answer twice on the same
    # callback query here.
    await _grant_daily_bonus(callback, db_user, session)
