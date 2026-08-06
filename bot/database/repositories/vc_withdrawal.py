from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update

from bot.database.models import VcWithdrawal
from bot.database.repositories.base import BaseRepository


class VcWithdrawalRepository(BaseRepository):
    async def create(
        self,
        user_id: int,
        vc_amount: Decimal,
        rate: Decimal,
        rp_debited: Decimal,
    ) -> VcWithdrawal:
        w = VcWithdrawal(user_id=user_id, vc_amount=vc_amount, rate=rate, rp_debited=rp_debited)
        self.session.add(w)
        await self.session.flush()
        return w

    async def get(self, withdrawal_id: int) -> VcWithdrawal | None:
        return await self.session.get(VcWithdrawal, withdrawal_id)

    async def pending_count(self) -> int:
        result = await self.session.execute(
            select(func.count(VcWithdrawal.id)).where(VcWithdrawal.status == "pending")
        )
        return result.scalar() or 0

    async def total_count(self) -> int:
        result = await self.session.execute(select(func.count(VcWithdrawal.id)))
        return result.scalar() or 0

    async def approve(self, withdrawal_id: int) -> VcWithdrawal | None:
        result = await self.session.execute(
            update(VcWithdrawal)
            .where(VcWithdrawal.id == withdrawal_id, VcWithdrawal.status == "pending")
            .values(status="approved", processed_at=datetime.utcnow())
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.session.get(VcWithdrawal, withdrawal_id)

    async def reject(self, withdrawal_id: int) -> VcWithdrawal | None:
        result = await self.session.execute(
            update(VcWithdrawal)
            .where(VcWithdrawal.id == withdrawal_id, VcWithdrawal.status == "pending")
            .values(status="rejected", processed_at=datetime.utcnow())
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.flush()
        return await self.session.get(VcWithdrawal, withdrawal_id)
