import random
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update

from bot.database.models import Lottery, LotteryTicket
from bot.database.repositories.base import BaseRepository

COMMISSION = 0.30


class LotteryRepository(BaseRepository):
    async def get_active(self) -> Lottery | None:
        result = await self.session.execute(
            select(Lottery).where(Lottery.status == "active").order_by(Lottery.id.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def user_ticket_count(self, lottery_id: int, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(LotteryTicket.id)).where(
                LotteryTicket.lottery_id == lottery_id,
                LotteryTicket.user_id == user_id,
            )
        )
        return result.scalar() or 0

    async def buy_ticket(self, lottery: Lottery, user_id: int) -> bool:
        prize_add = round(float(lottery.ticket_price) * (1 - COMMISSION), 2)
        result = await self.session.execute(
            update(Lottery)
            .where(
                Lottery.id == lottery.id,
                Lottery.status == "active",
                Lottery.tickets_sold == lottery.tickets_sold,
            )
            .values(
                tickets_sold=Lottery.tickets_sold + 1,
                total_collected=Lottery.total_collected + lottery.ticket_price,
                prize_pool=Lottery.prize_pool + Decimal(str(prize_add)),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False
        self.session.add(LotteryTicket(lottery_id=lottery.id, user_id=user_id))
        await self.session.commit()
        await self.session.refresh(lottery)
        return True

    async def get_participants(self, lottery_id: int) -> list[tuple]:
        result = await self.session.execute(
            select(
                LotteryTicket.user_id,
                func.count(LotteryTicket.id).label("cnt"),
            ).where(LotteryTicket.lottery_id == lottery_id)
            .group_by(LotteryTicket.user_id)
        )
        return result.all()

    async def draw_random(self, lottery: Lottery) -> int | None:
        tickets = (await self.session.execute(
            select(LotteryTicket).where(LotteryTicket.lottery_id == lottery.id)
        )).scalars().all()
        if not tickets:
            return None
        return random.choice(tickets).user_id

    async def finish(self, lottery: Lottery, winner_id: int) -> bool:
        drawn_at = datetime.utcnow()
        result = await self.session.execute(
            update(Lottery)
            .where(
                Lottery.id == lottery.id,
                Lottery.status == "active",
                Lottery.tickets_sold == lottery.tickets_sold,
                Lottery.prize_pool == lottery.prize_pool,
            )
            .values(
                status="finished",
                winner_id=winner_id,
                drawn_at=drawn_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False
        lottery.status = "finished"
        lottery.winner_id = winner_id
        lottery.drawn_at = drawn_at
        await self.session.commit()
        return True

    async def cancel(self, lottery: Lottery) -> bool:
        result = await self.session.execute(
            update(Lottery)
            .where(
                Lottery.id == lottery.id,
                Lottery.status == "active",
                Lottery.tickets_sold == 0,
            )
            .values(status="finished")
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False
        lottery.status = "finished"
        await self.session.commit()
        return True

    async def create(
        self,
        ticket_price: float,
        end_type: str,
        end_value: float,
        ticket_limit: int = 0,
        ref_required: int = 0,
        channel_id: str | None = None,
    ) -> Lottery:
        lot = Lottery(
            ticket_price=ticket_price,
            end_type=end_type,
            end_value=end_value,
            ticket_limit=ticket_limit,
            ref_required=ref_required,
            channel_id=channel_id,
        )
        self.session.add(lot)
        await self.session.commit()
        return lot

    async def check_auto_draw(self, lottery: Lottery) -> bool:
        if lottery.end_type == "tickets" and lottery.tickets_sold >= int(lottery.end_value):
            return True
        if lottery.end_type == "commission" and float(lottery.total_collected) >= float(lottery.end_value):
            return True
        if lottery.end_type == "time" and datetime.utcnow().timestamp() >= float(lottery.end_value):
            return True
        return False
