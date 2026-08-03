from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from bot.database.models import ChatPromoCode, ChatPromoUse
from bot.database.repositories.base import BaseRepository


class ChatPromoRepository(BaseRepository):
    async def get_by_code(self, chat_id: int, code: str) -> ChatPromoCode | None:
        result = await self.session.execute(
            select(ChatPromoCode).where(
                ChatPromoCode.chat_id == chat_id,
                ChatPromoCode.code == code.upper(),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, chat_id: int, code: str, created_by: int) -> ChatPromoCode:
        promo = ChatPromoCode(chat_id=chat_id, code=code.upper(), created_by=created_by)
        self.session.add(promo)
        await self.session.commit()
        return promo

    async def use(self, promo: ChatPromoCode, user_id: int, reward_amount: Decimal) -> bool:
        result = await self.session.execute(
            update(ChatPromoCode)
            .where(
                ChatPromoCode.id == promo.id,
                ChatPromoCode.is_active == True,  # noqa: E712
                or_(ChatPromoCode.usage_limit == 0, ChatPromoCode.used_count < ChatPromoCode.usage_limit),
            )
            .values(used_count=ChatPromoCode.used_count + 1)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False

        self.session.add(
            ChatPromoUse(code_id=promo.id, user_id=user_id, reward_amount=reward_amount)
        )
        try:
            await self.session.flush()
            return True
        except IntegrityError:
            await self.session.rollback()
            return False
