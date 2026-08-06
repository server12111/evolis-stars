from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import WithdrawalCounter


async def next_withdrawal_number(session: AsyncSession) -> int:
    """Atomically allocate the next "Заявка #N" number, shared across both
    Stars and VC withdrawals so admins see one continuous sequence in the
    channel regardless of currency -- two separate per-table autoincrement
    ids would otherwise both start counting from the same place and collide.

    Not committed here: this UPDATE lives in the caller's own transaction
    around the withdrawal row it's stamped onto, so a later rollback (e.g.
    the admin-channel send failing) undoes the allocation together with the
    row, exactly like WithdrawalRepository/VcWithdrawalRepository's own
    `create()` only flushes and leaves committing to the caller.
    """
    result = await session.execute(
        update(WithdrawalCounter)
        .where(WithdrawalCounter.id == 1)
        .values(value=WithdrawalCounter.value + 1)
    )
    if result.rowcount != 1:
        # Counter row missing (shouldn't happen once init_db's migration has
        # run) -- seed it rather than crash a withdrawal over it.
        session.add(WithdrawalCounter(id=1, value=1))
        await session.flush()
        return 1
    row = await session.execute(select(WithdrawalCounter.value).where(WithdrawalCounter.id == 1))
    return row.scalar_one()
