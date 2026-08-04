from bot.database.repositories.settings import SettingsRepository
from sqlalchemy.ext.asyncio import AsyncSession


def mines_coeff(mines: int, opened: int, house_edge: float = 0.20, max_coeff: float = 10.0) -> float:
    if opened == 0:
        return 1.0
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
