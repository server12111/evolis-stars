import asyncio
import logging
from datetime import datetime, timedelta

from bot.database.engine import SessionFactory
from bot.database.repositories.virus import VirusInfectionRepository
from bot.services.chat_eligibility import credit_stars
from bot.services.virus_game import VIRUS_TYPES

logger = logging.getLogger(__name__)

_TICK_SECONDS = 300  # finer than an hour so a restart never loses/duplicates a partial hour's payout


async def _pay_out_tick() -> None:
    async with SessionFactory() as session:
        repo = VirusInfectionRepository(session)
        for infection in await repo.list_active():
            now = datetime.utcnow()
            elapsed_hours = int((now - infection.last_payout_at).total_seconds() // 3600)
            if elapsed_hours < 1:
                continue
            hourly = VIRUS_TYPES[infection.virus_type].hourly_income
            new_last_payout_at = infection.last_payout_at + timedelta(hours=elapsed_hours)
            # Optimistic advance first -- if it loses a race (e.g. the
            # infection was just cured/deleted, or another tick already
            # advanced it), skip the credit entirely rather than paying
            # for hours that no longer belong to a live infection.
            advanced = await repo.advance_payout(
                infection.infected_user_id, infection.last_payout_at, new_last_payout_at,
            )
            if not advanced:
                continue
            await credit_stars(session, infection.infector_user_id, hourly * elapsed_hours)


async def virus_income_loop() -> None:
    while True:
        try:
            await _pay_out_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Virus income scheduler error")
        await asyncio.sleep(_TICK_SECONDS)
