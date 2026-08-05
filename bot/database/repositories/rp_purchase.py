from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from bot.database.models import RpPurchase
from bot.database.repositories.base import BaseRepository


class RpPurchaseRepository(BaseRepository):
    async def record(
        self,
        user_id: int,
        stars_paid: int,
        rp_credited: Decimal,
        rate_at_purchase: Decimal,
        telegram_payment_charge_id: str,
    ) -> RpPurchase | None:
        """Returns None (instead of raising) if this exact Telegram payment
        was already recorded — the caller must not credit RP⭐️ again in
        that case."""
        purchase = RpPurchase(
            user_id=user_id,
            stars_paid=stars_paid,
            rp_credited=rp_credited,
            rate_at_purchase=rate_at_purchase,
            telegram_payment_charge_id=telegram_payment_charge_id,
        )
        self.session.add(purchase)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return None
        return purchase

    async def total_count(self) -> int:
        result = await self.session.execute(select(func.count(RpPurchase.id)))
        return int(result.scalar() or 0)

    async def total_rp_purchased(self) -> Decimal:
        result = await self.session.execute(select(func.coalesce(func.sum(RpPurchase.rp_credited), 0)))
        return Decimal(str(result.scalar() or 0))
