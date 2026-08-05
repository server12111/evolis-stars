from datetime import datetime

from sqlalchemy import func, select, update

from bot.database.models import Withdrawal
from bot.database.repositories.base import BaseRepository


class WithdrawalRepository(BaseRepository):
    async def create(self, user_id: int, amount: float, recipient_username: str) -> Withdrawal:
        w = Withdrawal(user_id=user_id, amount=amount, recipient_username=recipient_username)
        self.session.add(w)
        await self.session.flush()
        return w

    async def get(self, withdrawal_id: int) -> Withdrawal | None:
        return await self.session.get(Withdrawal, withdrawal_id)

    async def pending_count(self) -> int:
        result = await self.session.execute(
            select(func.count(Withdrawal.id)).where(Withdrawal.status == "pending")
        )
        return result.scalar() or 0

    async def approved_sum(self) -> float:
        result = await self.session.execute(
            select(func.sum(Withdrawal.amount)).where(Withdrawal.status == "approved")
        )
        return float(result.scalar() or 0)

    async def rejected_count(self) -> int:
        result = await self.session.execute(
            select(func.count(Withdrawal.id)).where(Withdrawal.status == "rejected")
        )
        return result.scalar() or 0

    async def approve(self, withdrawal_id: int) -> Withdrawal | None:
        result = await self.session.execute(
            update(Withdrawal)
            .where(Withdrawal.id == withdrawal_id, Withdrawal.status == "pending")
            .values(status="approved", processed_at=datetime.utcnow())
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.session.get(Withdrawal, withdrawal_id)

    async def reject(self, withdrawal_id: int) -> Withdrawal | None:
        result = await self.session.execute(
            update(Withdrawal)
            .where(Withdrawal.id == withdrawal_id, Withdrawal.status == "pending")
            .values(status="rejected", processed_at=datetime.utcnow())
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.flush()
        return await self.session.get(Withdrawal, withdrawal_id)
