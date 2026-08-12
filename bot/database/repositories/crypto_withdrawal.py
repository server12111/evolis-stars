from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update

from bot.database.models import CryptoWithdrawal
from bot.database.repositories.base import BaseRepository


class CryptoWithdrawalRepository(BaseRepository):
    async def create(
        self,
        user_id: int,
        method: str,
        rp_amount: Decimal,
        payout_amount: Decimal,
        ton_rate_usd: Decimal | None,
        recipient: str,
    ) -> CryptoWithdrawal:
        w = CryptoWithdrawal(
            user_id=user_id,
            method=method,
            rp_amount=rp_amount,
            payout_amount=payout_amount,
            ton_rate_usd=ton_rate_usd,
            recipient=recipient,
        )
        self.session.add(w)
        await self.session.flush()
        return w

    async def get(self, withdrawal_id: int) -> CryptoWithdrawal | None:
        return await self.session.get(CryptoWithdrawal, withdrawal_id)

    async def pending_count(self) -> int:
        result = await self.session.execute(
            select(func.count(CryptoWithdrawal.id)).where(CryptoWithdrawal.status == "pending")
        )
        return result.scalar() or 0

    async def total_count(self) -> int:
        result = await self.session.execute(select(func.count(CryptoWithdrawal.id)))
        return result.scalar() or 0

    async def approve(self, withdrawal_id: int) -> CryptoWithdrawal | None:
        result = await self.session.execute(
            update(CryptoWithdrawal)
            .where(CryptoWithdrawal.id == withdrawal_id, CryptoWithdrawal.status == "pending")
            .values(status="approved", processed_at=datetime.utcnow())
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.session.get(CryptoWithdrawal, withdrawal_id)

    async def reject(self, withdrawal_id: int) -> CryptoWithdrawal | None:
        result = await self.session.execute(
            update(CryptoWithdrawal)
            .where(CryptoWithdrawal.id == withdrawal_id, CryptoWithdrawal.status == "pending")
            .values(status="rejected", processed_at=datetime.utcnow())
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.flush()
        return await self.session.get(CryptoWithdrawal, withdrawal_id)
