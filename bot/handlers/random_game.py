import random
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import GameSession, User
from bot.database.repositories.settings import SettingsRepository
from bot.services.casino import get_case_outcome, get_wheel_outcome, update_casino_profit

router = Router()

# Only games with a pure server-side RNG outcome (no live Telegram dice
# animation needed) are eligible — Wheel and the 3⭐ case both already match
# the fixed 3⭐ virtual stake this button hands out for free.
_POOL = ("wheel", "case_3")


@router.callback_query(lambda c: c.data == "menu:random")
async def cb_random(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    settings_repo = SettingsRepository(session)
    cooldown_hours = await settings_repo.get_int("random_cooldown_hours", 24)
    now = datetime.utcnow()

    if db_user.last_random_at and now - db_user.last_random_at < timedelta(hours=cooldown_hours):
        remaining = timedelta(hours=cooldown_hours) - (now - db_user.last_random_at)
        hours, rem_seconds = divmod(int(remaining.total_seconds()), 3600)
        minutes = rem_seconds // 60
        await callback.answer(
            f"⏳ «Рандом» доступен раз в {cooldown_hours} ч. Осталось: {hours}ч {minutes}м.",
            show_alert=True,
        )
        return

    stake = await settings_repo.get_float("random_stake", 3.0)
    choice = random.choice(_POOL)

    if choice == "wheel":
        coeff = await get_wheel_outcome(session)
        payout = round(stake * coeff, 2)
        game_label = "🎰 Колесо"
        detail = f"Множитель: <b>×{coeff:.1f}</b>\n"
    else:
        payout = await get_case_outcome(session, 3)
        game_label = "🎁 Кейс 3⭐"
        detail = ""

    db_user.last_random_at = now
    if payout > 0:
        db_user.stars_balance = Decimal(str(db_user.stars_balance)) + Decimal(str(payout))
    session.add(
        GameSession(
            user_id=db_user.user_id,
            game_type=choice,
            bet=Decimal("0"),
            payout=Decimal(str(payout)),
            result="win" if payout > 0 else "lose",
        )
    )
    await update_casino_profit(session, choice, stake, payout)
    await session.commit()

    text = (
        f"🎲 <b>Рандом!</b>\n\n"
        f"Тебе выпала бесплатная игра: {game_label}\n"
        f"{detail}"
        f"Бесплатная ставка: <b>{stake:.2f} ⭐</b>\n"
        f"Выигрыш: <b>+{payout:.2f} ⭐</b>\n\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()
