from bot.database.repositories.settings import SettingsRepository
from sqlalchemy.ext.asyncio import AsyncSession

# Explicit payout tables for specific mine counts, indexed by number of
# safe cells opened so far (1st entry = coeff after 1 safe cell, etc.).
# Board is always 5x5 = 25 cells, so len(table) == 25 - mines for each key.
# Mine counts not listed here (currently just 10) fall back to the
# fair-odds formula below, unaffected by these tables.
MINES_COEFF_TABLES: dict[int, list[float]] = {
    3: [
        0.80, 0.99, 1.20, 1.25, 1.30, 1.35, 1.45, 1.60, 1.70, 1.75,
        2.00, 2.20, 2.45, 2.75, 3.10, 3.50, 4.00, 4.70, 5.60, 6.50,
        8.00, 10.00,
    ],
    5: [
        0.90, 1.20, 1.40, 1.60, 1.70, 1.80, 2.00, 2.20, 2.50, 2.90,
        3.40, 4.00, 4.80, 5.80, 7.20, 8.00, 10.50, 13.00, 15.00, 19.00,
    ],
    15: [1.70, 2.20, 3.00, 4.20, 6.00, 9.00, 12.00, 18.00, 25.00, 32.00],
}


def mines_coeff(mines: int, opened: int, house_edge: float = 0.20, max_coeff: float = 10.0) -> float:
    if opened == 0:
        return 1.0
    table = MINES_COEFF_TABLES.get(mines)
    if table is not None:
        # opened is always <= len(table) in practice (can't open more cells
        # than there are safe ones), but clamp defensively.
        return table[min(opened, len(table)) - 1]
    prob = 1.0
    for i in range(opened):
        prob *= (25 - mines - i) / (25 - i)
    if prob <= 0:
        return 0.0
    raw = (1 / prob) * (1 - house_edge)
    return round(min(raw, max_coeff), 4)


async def get_mines_params(session: AsyncSession, punish: bool = False) -> tuple[float, float]:
    """Returns (house_edge, max_coeff)."""
    repo = SettingsRepository(session)
    if punish:
        house_edge = await repo.get_float("mines_house_edge_punish", 0.45)
    else:
        house_edge = await repo.get_float("mines_house_edge", 0.20)
    max_coeff = await repo.get_float("mines_max_coeff", 10.0)
    return house_edge, max_coeff
