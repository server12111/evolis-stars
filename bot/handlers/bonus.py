import random
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.settings import SettingsRepository

router = Router()

BONUS_COOLDOWN_HOURS = 24


@router.callback_query(lambda c: c.data == "menu:bonus")
async def cb_bonus(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
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
