import asyncio
import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from aiogram import Bot
from sqlalchemy import and_, or_, select, update

from bot.database.engine import SessionFactory
from bot.database.models import Duel, User
from bot.database.repositories.settings import SettingsRepository

logger = logging.getLogger(__name__)
_MONEY_STEP = Decimal("0.01")


def compute_duel_payout(amount: Decimal, commission_percent: float) -> Decimal:
    """Winner payout for a duel: both stakes minus the house commission."""
    commission = Decimal(str(min(100.0, max(0.0, commission_percent)) / 100))
    return (amount * 2 * (1 - commission)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


async def _notify(bot: Bot, user_id: int | None, text: str) -> None:
    if not user_id:
        return
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Failed to send duel expiry notification to %s: %s", user_id, exc)


async def _credit(session, user_id: int | None, amount: Decimal) -> None:
    if not user_id:
        return
    await session.execute(
        update(User).where(User.user_id == user_id).values(stars_balance=User.stars_balance + amount)
    )


async def _settle_expired_duel(duel: Duel, bot: Bot) -> None:
    notifications: list[tuple[int | None, str]] = []
    async with SessionFactory() as session:
        current = await session.get(Duel, duel.id)
        if (
            current is None
            or current.status not in {"waiting", "confirming", "active"}
            or current.expires_at is None
        ):
            return
        rolls_ready = (
            current.status == "active"
            and current.creator_roll is not None
            and current.joiner_roll is not None
        )
        if current.expires_at > datetime.utcnow() and not rolls_ready:
            return

        original_status = current.status
        claim = await session.execute(
            update(Duel)
            .where(Duel.id == current.id, Duel.status == original_status)
            .values(status="settling")
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount != 1:
            await session.rollback()
            return
        current.status = "settling"

        amount = Decimal(current.amount)
        if original_status == "waiting":
            await _credit(session, current.creator_id, amount)
            current.status = "cancelled"
            notifications.append(
                (
                    current.creator_id,
                    f"⏰ Дуэль #{current.id} отменена: никто не присоединился. "
                    f"<b>{amount:.2f} RP⭐️</b> возвращено.",
                )
            )
        elif original_status == "confirming":
            await _credit(session, current.creator_id, amount)
            await _credit(session, current.joiner_id, amount)
            current.status = "cancelled"
            text = (
                f"⏰ Дуэль #{current.id} отменена: подтверждение не получено вовремя. "
                f"<b>{amount:.2f} RP⭐️</b> возвращено."
            )
            notifications.extend(
                [(current.creator_id, text), (current.joiner_id, text)]
            )
        else:
            creator_rolled = current.creator_roll is not None
            joiner_rolled = current.joiner_roll is not None
            current.status = "finished"

            if creator_rolled and joiner_rolled:
                if current.creator_roll == current.joiner_roll:
                    await _credit(session, current.creator_id, amount)
                    await _credit(session, current.joiner_id, amount)
                    text = (
                        f"🤝 Дуэль #{current.id} завершена вничью после перезапуска. "
                        "Ставки возвращены."
                    )
                    notifications.extend(
                        [(current.creator_id, text), (current.joiner_id, text)]
                    )
                else:
                    commission_percent = await SettingsRepository(session).get_float(
                        "duel_commission",
                        20.0,
                    )
                    winner_id = (
                        current.creator_id
                        if current.creator_roll > current.joiner_roll
                        else current.joiner_id
                    )
                    payout = compute_duel_payout(amount, commission_percent)
                    await _credit(session, winner_id, payout)
                    current.winner_id = winner_id
                    text = (
                        f"🏆 Дуэль #{current.id} завершена после перезапуска. "
                        f"Победителю начислено <b>{payout:.2f} RP⭐️</b>."
                    )
                    notifications.extend(
                        [(current.creator_id, text), (current.joiner_id, text)]
                    )
            elif creator_rolled or joiner_rolled:
                winner_id = (
                    current.creator_id if creator_rolled else current.joiner_id
                )
                loser_id = (
                    current.joiner_id if creator_rolled else current.creator_id
                )
                await _credit(session, winner_id, amount)
                current.winner_id = winner_id
                notifications.extend(
                    [
                        (
                            winner_id,
                            f"⏰ Соперник не сделал ход в дуэли #{current.id}. "
                            "Ваша ставка возвращена.",
                        ),
                        (
                            loser_id,
                            f"⏰ Вы не сделали ход в дуэли #{current.id}; ставка сгорела.",
                        ),
                    ]
                )
            else:
                await _credit(session, current.creator_id, amount)
                await _credit(session, current.joiner_id, amount)
                text = (
                    f"⏰ В дуэли #{current.id} никто не сделал ход. Ставки возвращены."
                )
                notifications.extend(
                    [(current.creator_id, text), (current.joiner_id, text)]
                )
        await session.commit()

    for user_id, text in notifications:
        await _notify(bot, user_id, text)


async def duel_expiry_loop(bot: Bot) -> None:
    """Recover and settle persisted duels even after a process crash."""
    while True:
        try:
            async with SessionFactory() as session:
                result = await session.execute(
                    select(Duel).where(
                        Duel.status.in_(("waiting", "confirming", "active")),
                        Duel.expires_at.is_not(None),
                        or_(
                            Duel.expires_at <= datetime.utcnow(),
                            and_(
                                Duel.status == "active",
                                Duel.creator_roll.is_not(None),
                                Duel.joiner_roll.is_not(None),
                            ),
                        ),
                    )
                )
                expired = list(result.scalars().all())
            for duel in expired:
                await _settle_expired_duel(duel, bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Duel recovery scheduler error")
        await asyncio.sleep(15)
