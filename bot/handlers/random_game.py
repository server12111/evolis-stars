import random
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository

router = Router()

# The user plays this themselves now (no auto-play), so any game reachable
# from the main menu works — not just the pure-RNG ones a silent auto-play
# was previously limited to.
_POOL = {
    "wheel": ("🎡 Колесо", "menu:wheel"),
    "cases": ("🎁 Кейсы", "menu:cases"),
    "mines": ("💣 Мины", "menu:mines"),
    "tower": ("🗼 Башня", "menu:tower"),
}


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
    choice = random.choice(list(_POOL))
    game_label, deep_link = _POOL[choice]

    db_user.last_random_at = now
    db_user.stars_balance = round(float(db_user.stars_balance) + stake, 2)
    await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"🎮 Играть — {game_label}", callback_data=deep_link))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))

    text = (
        f"🎲 <b>Рандом!</b>\n\n"
        f"Тебе повезло — бесплатная ставка <b>{stake:.2f} ⭐</b> уже зачислена на баланс.\n"
        f"Разыграй её здесь: {game_label}\n\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>"
    )
    await callback.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()
