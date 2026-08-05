import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from sqlalchemy import update

from bot.database.engine import SessionFactory
from bot.database.models import User

logger = logging.getLogger(__name__)


async def auction_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            async with SessionFactory() as session:
                from bot.database.repositories.auction import AuctionRepository
                from bot.database.repositories.user import UserRepository
                from bot.database.repositories.settings import SettingsRepository

                s_repo = SettingsRepository(session)
                enabled = await s_repo.get_bool("auction_enabled", True)
                if not enabled:
                    continue

                repo = AuctionRepository(session)
                round_ = await repo.get_active()

                if round_ is None:
                    await repo.create_new()
                    logger.info("Auction: created new round")
                    continue

                now = datetime.utcnow()
                if now < round_.end_at:
                    continue

                # Time to finish
                commission = await s_repo.get_float("auction_commission", 0.20)
                winner_share = round(float(round_.prize_pool) * (1 - commission), 2)

                winner_id = round_.current_bidder_id

                if winner_id:
                    user_repo = UserRepository(session)
                    winner = await user_repo.get(winner_id)
                    if winner:
                        await session.execute(
                            update(User).where(User.user_id == winner_id).values(
                                stars_balance=User.stars_balance + winner_share
                            )
                        )
                    if not await repo.finish(round_):
                        continue
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
                        continue
                    logger.info("Auction round %s finished with no bids.", round_.id)

                # Create next round
                await repo.create_new()
                logger.info("Auction: new round started")

        except Exception:
            logger.exception("Auction scheduler error")
