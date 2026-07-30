from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from bot.database.models import PromoCode, PromoUse
from bot.database.repositories.base import BaseRepository


class PromoRepository(BaseRepository):
    async def get_by_code(self, code: str) -> PromoCode | None:
        result = await self.session.execute(
            select(PromoCode).where(PromoCode.code == code.upper())
        )
        return result.scalar_one_or_none()

    async def already_used(self, code_id: int, user_id: int) -> bool:
        result = await self.session.execute(
            select(PromoUse.id).where(
                PromoUse.code_id == code_id,
                PromoUse.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def use(self, promo: PromoCode, user_id: int) -> bool:
        now = datetime.utcnow()
        result = await self.session.execute(
            update(PromoCode)
            .where(
                PromoCode.id == promo.id,
                PromoCode.is_active == True,
                or_(PromoCode.expires_at.is_(None), PromoCode.expires_at >= now),
                or_(PromoCode.usage_limit == 0, PromoCode.used_count < PromoCode.usage_limit),
            )
            .values(used_count=PromoCode.used_count + 1)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False

        self.session.add(PromoUse(code_id=promo.id, user_id=user_id))
        try:
            await self.session.flush()
            return True
        except IntegrityError:
            await self.session.rollback()
            return False

    async def create(
        self,
        code: str,
        reward: float,
        usage_limit: int = 0,
        expires_at: datetime | None = None,
    ) -> PromoCode:
        p = PromoCode(
            code=code.upper(),
            reward_amount=reward,
            usage_limit=usage_limit,
            expires_at=expires_at,
        )
        self.session.add(p)
        await self.session.commit()
        return p

    async def all_active(self) -> list[PromoCode]:
        result = await self.session.execute(
            select(PromoCode).where(PromoCode.is_active == True).order_by(PromoCode.id.desc())
        )
        return list(result.scalars().all())

    async def delete(self, promo_id: int) -> bool:
        p = await self.session.get(PromoCode, promo_id)
        if p:
            await self.session.delete(p)
            await self.session.commit()
            return True
        return False
