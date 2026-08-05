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

    async def get_by_chat(self, chat_id: int) -> ChatPromoCode | None:
        """Any promo code already created in this chat — used to enforce
        "one promo code creation per chat" (only the owner can create one,
        and a chat has exactly one owner)."""
        result = await self.session.execute(
            select(ChatPromoCode).where(ChatPromoCode.chat_id == chat_id).limit(1)
        )
        return result.scalars().first()

    async def create(self, chat_id: int, code: str, created_by: int) -> ChatPromoCode | None:
        """Returns None (instead of raising) if a promo with this exact
        code already exists in this chat — the (chat_id, code) unique
        constraint is the real guard; the caller's prior get_by_code()/
        get_by_chat() checks are just an optimization to skip the round
        trip in the common case, not a race-proof guarantee on their own
        (group chats have no per-user lock, so the same code sent twice in
        quick succession can reach here concurrently)."""
        promo = ChatPromoCode(chat_id=chat_id, code=code.upper(), created_by=created_by)
        self.session.add(promo)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return None
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
