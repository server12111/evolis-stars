from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bot.database.models import ChatBonusCode, ChatBonusSponsor, ChatBonusUse
from bot.database.repositories.base import BaseRepository

MAX_BONUS_SPONSORS = 3


class ChatBonusSponsorRepository(BaseRepository):
    async def list_for_bonus(self, bonus_id: int) -> list[ChatBonusSponsor]:
        result = await self.session.execute(
            select(ChatBonusSponsor).where(ChatBonusSponsor.bonus_id == bonus_id).order_by(ChatBonusSponsor.id)
        )
        return list(result.scalars().all())

    async def list_previously_used_by_owner(self, owner_user_id: int, sponsor_type: str) -> list[ChatBonusSponsor]:
        """Distinct past sponsors (across any of this owner's bonuses) —
        lets a new bonus reuse one without repeating the "promote the bot
        to admin" flow, whose deep link fires no my_chat_member event (so
        silently does nothing) when the bot is already an admin there."""
        result = await self.session.execute(
            select(ChatBonusSponsor)
            .join(ChatBonusCode, ChatBonusCode.id == ChatBonusSponsor.bonus_id)
            .where(
                ChatBonusCode.created_by == owner_user_id,
                ChatBonusSponsor.sponsor_type == sponsor_type,
            )
            .order_by(ChatBonusSponsor.id.desc())
        )
        seen: set[int] = set()
        sponsors: list[ChatBonusSponsor] = []
        for sponsor in result.scalars().all():
            if sponsor.sponsor_chat_id in seen:
                continue
            seen.add(sponsor.sponsor_chat_id)
            sponsors.append(sponsor)
        return sponsors

    async def add(
        self, bonus_id: int, sponsor_chat_id: int, sponsor_type: str, title: str, username: str | None,
    ) -> ChatBonusSponsor | None:
        """Returns None (no-op) if this chat is already a sponsor of this
        bonus or the 3-sponsor cap is already reached — both checked
        atomically enough for this use case (single owner adds sponsors
        one at a time via a slow, manual Telegram admin-rights flow)."""
        existing = await self.list_for_bonus(bonus_id)
        if len(existing) >= MAX_BONUS_SPONSORS:
            return None
        if any(s.sponsor_chat_id == sponsor_chat_id for s in existing):
            return None
        sponsor = ChatBonusSponsor(
            bonus_id=bonus_id, sponsor_chat_id=sponsor_chat_id, sponsor_type=sponsor_type,
            title=title, username=username,
        )
        self.session.add(sponsor)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return None
        await self.session.commit()
        return sponsor


class ChatBonusRepository(BaseRepository):
    async def get_by_code(self, chat_id: int, code: str) -> ChatBonusCode | None:
        result = await self.session.execute(
            select(ChatBonusCode).where(
                ChatBonusCode.chat_id == chat_id,
                ChatBonusCode.code == code.upper(),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, bonus_id: int) -> ChatBonusCode | None:
        return await self.session.get(ChatBonusCode, bonus_id)

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
    ) -> ChatBonusCode | None:
        """Returns None (instead of raising) if a bonus with this exact code
        already exists in this chat — group chats have no per-user lock, so
        the owner's client double-sending the same code text can reach here
        concurrently before either commits. The caller has already debited
        total_charged from the owner's balance and MUST refund it when this
        returns None, since no bonus was actually created."""
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
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return None
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
