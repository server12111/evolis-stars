from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.states.games import GameStates, MinesStates, TowerStates

_SIDE_SELECT_STATES = {
    GameStates.choose_dice_side.state,
    GameStates.choose_football_side.state,
    GameStates.choose_basketball_side.state,
    GameStates.choose_bowling_side.state,
    GameStates.choose_darts_side.state,
}


async def guard_active_game(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext,
) -> bool:
    """Every "go to a hub screen" handler (menu:main, menu:casino,
    menu:games, ...) used to just call state.clear() with no regard for
    whatever game was mid-flight — reachable any time a user taps a STALE
    hub button left over from an older message while a real paid game is
    still active. Each game already protects its OWN dedicated exit
    button (mines:quit, tower:quit, games.py's menu:games handler) with
    a refund-if-no-progress / block-if-progress rule; this re-applies the
    exact same rule so every other hub entry point is equally safe.

    Returns True if the caller must stop (an alert was shown, nothing
    else should happen); False if it's safe to proceed as normal.
    """
    current = await state.get_state()

    if current in _SIDE_SELECT_STATES:
        # No progress is possible at a "choose a side" step — the bet was
        # taken but nothing has been decided yet, so it always refunds.
        data = await state.get_data()
        bet = data.get("bet", 0.0)
        if bet:
            db_user.stars_balance = round(float(db_user.stars_balance) + float(bet), 2)
            await session.commit()
        await state.clear()
        return False

    if current == MinesStates.playing.state:
        data = await state.get_data()
        if data.get("gems", 0) != 0:
            await callback.answer("⚠️ Сначала заберите выигрыш или откройте мину!", show_alert=True)
            return True
        bet = data.get("bet", 0)
        if bet and not data.get("used_free_credit", False):
            db_user.stars_balance = round(float(db_user.stars_balance) + bet, 2)
            await session.commit()
        await state.clear()
        return False

    if current == TowerStates.playing.state:
        data = await state.get_data()
        if data.get("level", 0) != 0:
            await callback.answer("⚠️ Сначала заберите выигрыш!", show_alert=True)
            return True
        bet = data.get("bet", 0)
        if bet and not data.get("used_free_credit", False):
            db_user.stars_balance = round(float(db_user.stars_balance) + bet, 2)
            await session.commit()
        await state.clear()
        return False

    return False
