from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bot.database.models import ChatBonusCode, ChatBonusUse
from bot.database.repositories.base import BaseRepository


class ChatBonusRepository(BaseRepository):
    async def get_by_code(self, chat_id: int, code: str) -> ChatBonusCode | None:
        result = await self.session.execute(
            select(ChatBonusCode).where(
                ChatBonusCode.chat_id == chat_id,
                ChatBonusCode.code == code.upper(),
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        chat_id: int,
        code: str,
        reward_amount: Decimal,
        usage_limit: int,
        commission_rate: Decimal,
        mode: str,
        min_days_in_chat: int,
        min_messages: int,
        condition_note: str | None,
        created_by: int,
    ) -> ChatBonusCode:
        total_charged = (reward_amount * usage_limit * (1 + commission_rate)).quantize(Decimal("0.01"))
        bonus = ChatBonusCode(
            chat_id=chat_id,
            code=code.upper(),
            reward_amount=reward_amount,
            usage_limit=usage_limit,
            commission_rate=commission_rate,
            total_charged=total_charged,
            mode=mode,
            min_days_in_chat=min_days_in_chat,
            min_messages=min_messages,
            condition_note=condition_note,
            created_by=created_by,
        )
        self.session.add(bonus)
        await self.session.commit()
        return bonus

    async def redeem(self, bonus: ChatBonusCode, user_id: int, awarded_by: int | None = None) -> bool:
        """Atomically claim one use for user_id — self-serve redemption or a
        contest owner's manual pick both go through this same guarded path."""
        result = await self.session.execute(
            update(ChatBonusCode)
            .where(
                ChatBonusCode.id == bonus.id,
                ChatBonusCode.is_active == True,  # noqa: E712
                ChatBonusCode.used_count < ChatBonusCode.usage_limit,
            )
            .values(used_count=ChatBonusCode.used_count + 1)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False

        self.session.add(
            ChatBonusUse(
                code_id=bonus.id,
                user_id=user_id,
                reward_amount=bonus.reward_amount,
                awarded_by=awarded_by,
            )
        )
        try:
            await self.session.flush()
            return True
        except IntegrityError:
            await self.session.rollback()
            return False
