from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ChatMembership, User


async def get_membership(session: AsyncSession, chat_id: int, user_id: int) -> ChatMembership | None:
    result = await session.execute(
        select(ChatMembership).where(
            ChatMembership.chat_id == chat_id,
            ChatMembership.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def eligibility_reason(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    min_days: int,
    min_messages: int,
) -> str | None:
    """None if eligible to redeem a chat promo/bonus, else a human-readable
    reason why not — covers the shared "registered in Evolis, N+ days in
    chat, N+ messages" gate used by both promo codes and bonus codes."""
    user = await session.get(User, user_id)
    if user is None:
        return "нужно быть зарегистрированным в боте (нажми /start в личных сообщениях)"

    membership = await get_membership(session, chat_id, user_id)
    if membership is None or membership.left_at is not None:
        return "не удалось подтвердить твоё членство в этом чате"

    if min_days > 0:
        required_since = datetime.utcnow() - timedelta(days=min_days)
        if membership.joined_at > required_since:
            return f"нужно быть в чате минимум {min_days} дн."

    if min_messages > 0 and membership.message_count < min_messages:
        return f"нужно написать минимум {min_messages} сообщений в чате (сейчас: {membership.message_count})"

    return None


async def credit_stars(session: AsyncSession, user_id: int, amount: Decimal) -> None:
    await session.execute(
        update(User).where(User.user_id == user_id).values(stars_balance=User.stars_balance + amount)
    )
    await session.commit()


async def debit_stars_if_enough(session: AsyncSession, user_id: int, amount: Decimal) -> bool:
    """Atomic conditional debit — no-ops (returns False) if the balance is
    insufficient, instead of ever going negative."""
    result = await session.execute(
        update(User)
        .where(User.user_id == user_id, User.stars_balance >= amount)
        .values(stars_balance=User.stars_balance - amount)
    )
    if result.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    return True
