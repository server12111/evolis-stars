import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import update

from bot.database.engine import SessionFactory
from bot.database.models import User

logger = logging.getLogger(__name__)


async def auction_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await _run_pass(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auction scheduler error")


async def _run_pass(bot: Bot) -> None:
    async with SessionFactory() as session:
        from bot.database.repositories.auction import AuctionRepository
        from bot.database.repositories.user import UserRepository
        from bot.database.repositories.settings import SettingsRepository

        s_repo = SettingsRepository(session)
        enabled = await s_repo.get_bool("auction_enabled", True)
        if not enabled:
            return

        repo = AuctionRepository(session)
        round_ = await repo.get_active()

        if round_ is None:
            await repo.create_new()
            logger.info("Auction: created new round")
            return

        now = datetime.utcnow()
        if now < round_.end_at:
            return

        # Time to finish
        commission = await s_repo.get_float("auction_commission", 0.20)
        winner_share = round(float(round_.prize_pool) * (1 - commission), 2)

        winner_id = round_.current_bidder_id

        if winner_id:
            user_repo = UserRepository(session)
            winner = await user_repo.get(winner_id)
            if not winner:
                # Don't close out the round over a phantom winner — the
                # prize would otherwise be marked paid without ever being
                # credited to anyone (mirrors lottery.py's same guard).
                # Leave the round active past its end_at so this pass
                # retries it again next tick.
                logger.warning(
                    "Auction round %s: winner_id=%s not found in users, deferring finish.",
                    round_.id, winner_id,
                )
                return
            await session.execute(
                update(User).where(User.user_id == winner_id).values(
                    # Must be Decimal, not float: `winner` was just loaded
                    # into this session's identity map above, so SQLAlchemy's
                    # default synchronize_session="evaluate" re-applies this
                    # expression in Python against that object — Decimal +
                    # float raises TypeError there, silently crashing the
                    # whole pass (caught by the outer try/except) and
                    # leaving the round unfinished and the winner unpaid,
                    # forever, every 60s.
                    stars_balance=User.stars_balance + Decimal(str(winner_share))
                )
            )
            if not await repo.finish(round_):
                return
            try:
                await bot.send_message(
                    winner_id,
                    f"🏆 <b>Вы выиграли аукцион!</b>\n\n"
                    f"Призовой фонд: <b>{float(round_.prize_pool):.2f} RP⭐️</b>\n"
                    f"Ваш выигрыш (80%): <b>+{winner_share:.2f} RP⭐️</b>\n"
                    f"Звёзды зачислены на баланс!",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            logger.info("Auction round %s finished. Winner %s, prize %.2f", round_.id, winner_id, winner_share)
        else:
            if not await repo.finish(round_):
                return
            logger.info("Auction round %s finished with no bids.", round_.id)

        # Create next round
        await repo.create_new()
        logger.info("Auction: new round started")
