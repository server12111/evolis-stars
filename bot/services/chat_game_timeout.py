import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import select

from bot.database.engine import SessionFactory
from bot.database.models import ChatGameRound, User
from bot.database.repositories.chat_game import ChatGameRoundRepository
from bot.services.chat_eligibility import credit_stars
from bot.services.chat_games import get_chat_tower_coeff, get_doors_coeff, get_maze_params, maze_base_coeff, record_result

logger = logging.getLogger(__name__)

ROUND_TIMEOUT = timedelta(minutes=2)
SWEEP_INTERVAL_SECONDS = 30

_LABELS = {"doors": "Двери", "maze": "Лабиринт", "tower": "Башня"}
_SETTINGS_PREFIX = {"doors": "doors", "maze": "maze", "tower": "chat_tower"}


async def chat_game_timeout_loop(bot: Bot) -> None:
    while True:
        try:
            async with SessionFactory() as session:
                await sweep_stale_rounds(bot, session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Chat game timeout sweep error")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


async def sweep_stale_rounds(bot: Bot, session) -> None:
    """Core sweep logic, factored out from the polling loop so it can be
    exercised directly (with an injected session) in tests."""
    cutoff = datetime.utcnow() - ROUND_TIMEOUT
    result = await session.execute(
        select(ChatGameRound).where(ChatGameRound.updated_at < cutoff)
    )
    rounds = list(result.scalars().all())
    for round_ in rounds:
        await _settle_timeout(bot, session, round_)


async def _resolve_coeff(session, game_type: str, round_: ChatGameRound, state: dict) -> float:
    if game_type == "doors":
        return await get_doors_coeff(session, round_.level)
    if game_type == "maze":
        house_edge, max_coeff = await get_maze_params(session)
        bonus = state.get("bonus", 0.0)
        return round(maze_base_coeff(round_.level, house_edge, max_coeff) + bonus, 4)
    if game_type == "tower":
        return await get_chat_tower_coeff(session, round_.level - 1)
    return 1.0


async def _settle_timeout(bot: Bot, session, round_: ChatGameRound) -> None:
    round_repo = ChatGameRoundRepository(session)
    bet = float(round_.bet)
    game_type = round_.game_type
    state = round_repo.load_state(round_)

    if round_.level == 0:
        # No progress made yet — a stuck round like this isn't the player's
        # fault, so return the stake in full instead of treating it as a loss.
        payout = bet
        note = "Ставка возвращена — прогресса не было."
    else:
        coeff = await _resolve_coeff(session, game_type, round_, state)
        payout = round(bet * coeff, 2)
        note = f"Выигрыш на достигнутом уровне забран автоматически (×{coeff:.2f})."

    await round_repo.delete(round_)
    if payout > 0:
        await credit_stars(session, round_.user_id, Decimal(str(payout)))
    prefix = _SETTINGS_PREFIX.get(game_type, game_type)
    await record_result(
        session, round_.user_id, round_.chat_id, game_type,
        bet, payout, f"{prefix}_total_bet", f"{prefix}_total_payout",
    )

    user = await session.get(User, round_.user_id)
    name = f"@{user.username}" if user and user.username else (user.first_name if user else str(round_.user_id))
    label = _LABELS.get(game_type, game_type)
    text = (
        f"⏱ Игра «{label}» у {name} завершена по тайм-ауту (2 минуты без действий).\n"
        f"{note}\nВыплата: <b>{payout:.2f} ⭐</b>"
    )
    try:
        await bot.send_message(round_.chat_id, text, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Cannot notify chat %s about timed-out round: %s", round_.chat_id, exc)
