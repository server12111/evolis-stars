import asyncio
import logging

from aiogram import Bot
from sqlalchemy import update

from bot.database.engine import SessionFactory
from bot.database.models import User

logger = logging.getLogger(__name__)


async def lottery_time_check_loop(bot: Bot) -> None:
    """Every 60s check active lotteries for time-based end condition."""
    while True:
        await asyncio.sleep(60)
        try:
            async with SessionFactory() as session:
                from bot.database.repositories.lottery import LotteryRepository
                from bot.database.repositories.user import UserRepository

                repo = LotteryRepository(session)
                lottery = await repo.get_active()
                if not lottery:
                    continue

                should_draw = await repo.check_auto_draw(lottery)
                if not should_draw or lottery.tickets_sold == 0:
                    continue

                winner_id = await repo.draw_random(lottery)
                if not winner_id:
                    continue

                user_repo = UserRepository(session)
                winner = await user_repo.get(winner_id)
                if winner:
                    await session.execute(
                        update(User).where(User.user_id == winner_id).values(
                            stars_balance=User.stars_balance + lottery.prize_pool
                        )
                    )

                if not await repo.finish(lottery, winner_id):
                    continue

                try:
                    await bot.send_message(
                        winner_id,
                        f"🎉 <b>Поздравляем! Вы выиграли лотерею!</b>\n\n"
                        f"🏆 Ваш выигрыш: <b>{lottery.prize_pool:.2f} ⭐</b>\n"
                        f"💰 Звёзды начислены на ваш баланс!",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

                logger.info("Lottery %s auto-drawn, winner %s, prize %.2f", lottery.id, winner_id, lottery.prize_pool)
        except Exception:
            logger.exception("Lottery scheduler error")
