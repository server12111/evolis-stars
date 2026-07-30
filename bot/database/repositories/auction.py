from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update

from bot.database.models import AuctionRound, AuctionBid
from bot.database.repositories.base import BaseRepository


class AuctionRepository(BaseRepository):
    async def get_active(self) -> AuctionRound | None:
        result = await self.session.execute(
            select(AuctionRound).where(AuctionRound.status == "active").order_by(AuctionRound.id.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def create_new(self) -> AuctionRound:
        now = datetime.utcnow()
        round_ = AuctionRound(start_at=now, end_at=now + timedelta(hours=8))
        self.session.add(round_)
        await self.session.commit()
        await self.session.refresh(round_)
        return round_

    async def place_bid(self, round_: AuctionRound, user_id: int, new_bid: Decimal, paid: Decimal) -> bool:
        result = await self.session.execute(
            update(AuctionRound)
            .where(
                AuctionRound.id == round_.id,
                AuctionRound.status == "active",
                AuctionRound.current_bid == round_.current_bid,
            )
            .values(
                current_bid=new_bid,
                current_bidder_id=user_id,
                prize_pool=AuctionRound.prize_pool + paid,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False
        bid = AuctionBid(round_id=round_.id, user_id=user_id, amount=new_bid)
        self.session.add(bid)
        round_.current_bid = new_bid
        round_.current_bidder_id = user_id
        round_.prize_pool = Decimal(str(round(float(round_.prize_pool) + float(paid), 2)))
        await self.session.commit()
        return True

    async def finish(self, round_: AuctionRound) -> bool:
        result = await self.session.execute(
            update(AuctionRound)
            .where(
                AuctionRound.id == round_.id,
                AuctionRound.status == "active",
                AuctionRound.current_bid == round_.current_bid,
            )
            .values(
                status="finished",
                winner_id=round_.current_bidder_id,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False
        round_.status = "finished"
        round_.winner_id = round_.current_bidder_id
        await self.session.commit()
        return True

    async def get_bids(self, round_id: int) -> list[AuctionBid]:
        result = await self.session.execute(
            select(AuctionBid).where(AuctionBid.round_id == round_id).order_by(AuctionBid.bid_at.desc())
        )
        return list(result.scalars().all())
