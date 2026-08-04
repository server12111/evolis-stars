from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.settings import SettingsRepository


async def is_in_recovery(session: AsyncSession, game_key: str) -> bool:
    """True when this game's all-time cumulative payout has caught up to (or
    passed) its cumulative bet — i.e. the house is currently net negative on
    this specific game. Read once per bet/round-start and never re-checked
    mid-round, so a game already in progress can't have its odds shift under
    the player.

    game_key values in use: "wheel", "case_1"/"case_3"/"case_5", "mines",
    "tower" (private), "roulette", "maze", "doors", "chat_tower" (group)."""
    repo = SettingsRepository(session)
    total_bet = await repo.get_float(f"{game_key}_total_bet")
    total_payout = await repo.get_float(f"{game_key}_total_payout")
    return total_payout >= total_bet and total_bet > 0
