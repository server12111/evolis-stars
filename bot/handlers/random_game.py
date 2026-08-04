import random
from datetime import datetime, timedelta
from decimal import Decimal

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

# Each press picks one of these at random — all three must actually occur.
RANDOM_STAKE_CHOICES = (1.0, 2.0, 3.0)


async def has_free_game_credit_for(session: AsyncSession, user: User, bet: float) -> bool:
    """Read-only check — does the user have an unused free-game credit that
    would cover this exact bet? Used for intermediate steps (choosing a
    bet, choosing a mine count, ...) where actually spending the credit
    would be premature since the user could still back out."""
    amount = user.free_game_credit_amount
    return amount is not None and float(amount) == bet


async def try_consume_free_game_credit(session: AsyncSession, user: User, bet: float) -> bool:
    """If the user holds an unused free-game credit for exactly this bet,
    consumes it and returns True — the caller must then NOT debit `bet`
    from the balance for this round (everything else, including payout
    math, proceeds exactly as if it were a normal paid bet). Returns False
    otherwise, in which case the caller debits the bet as usual. Call this
    only at the point the bet is actually committed, not at earlier
    "choose a bet" steps — see has_free_game_credit_for for those."""
    if not await has_free_game_credit_for(session, user, bet):
        return False
    user.free_game_credit_amount = None
    return True


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

    stake = random.choice(RANDOM_STAKE_CHOICES)
    choice = random.choice(list(_POOL))
    game_label, deep_link = _POOL[choice]

    db_user.last_random_at = now
    db_user.free_game_credit_amount = Decimal(str(stake))
    await session.commit()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"🎮 Играть — {game_label}", callback_data=deep_link))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))

    text = (
        f"🎲 <b>Рандом!</b>\n\n"
        f"Тебе повезло — бесплатная игра на <b>{stake:.2f} ⭐</b>! "
        f"Она не зачисляется на баланс, а хранится отдельно — сделай ставку ровно "
        f"<b>{stake:.2f} ⭐</b> в любой игре, и эта ставка спишется с бонуса, а не с баланса.\n"
        f"Попробуй здесь: {game_label}\n\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>"
    )
    await callback.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()
