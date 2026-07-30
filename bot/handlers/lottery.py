from datetime import datetime

from aiogram import Bot, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.lottery import LotteryRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.lottery import lottery_menu_kb
from bot.services.telegram_chat import is_subscribed, telegram_chat_id

router = Router()

def _lottery_text(lottery, user_tickets: int, balance: float) -> str:
    from bot.database.repositories.lottery import COMMISSION
    if lottery.end_type == "tickets":
        end_line = f"🎯 Розыгрыш при <b>{int(float(lottery.end_value))}</b> билетах"
    elif lottery.end_type == "time":
        dt = datetime.utcfromtimestamp(float(lottery.end_value))
        end_line = f"🗓 Дата: <b>{dt.strftime('%d.%m.%Y %H:%M')} UTC</b>"
    else:
        pool = round(float(lottery.end_value) * (1 - COMMISSION), 2)
        end_line = f"🎯 Розыгрыш при сборе <b>{float(lottery.end_value):.0f} ⭐</b> | Приз ~<b>{pool:.0f} ⭐</b>"

    return (
        f"🎟 <b>Лотерея</b>\n\n"
        f"💰 <b>Призовой пул: {float(lottery.prize_pool):.2f} ⭐</b>\n"
        f"🎫 Продано билетов: <b>{lottery.tickets_sold}</b>\n\n"
        f"{end_line}\n\n"
        f"📋 Цена билета: <b>{float(lottery.ticket_price):.0f} ⭐</b>\n\n"
        f"🎫 Твоих билетов: <b>{user_tickets}</b>\n"
        f"💳 Баланс: <b>{balance:.2f} ⭐</b>"
    )


@router.callback_query(lambda c: c.data == "game:lottery")
async def cb_lottery(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    min_refs = max(
        0,
        await SettingsRepository(session).get_int("lottery_min_refs", 3),
    )
    if db_user.referrals_count < min_refs:
        await callback.answer(
            f"❌ Нужно минимум {min_refs} реферала. Твоих: {db_user.referrals_count}/{min_refs}",
            show_alert=True,
        )
        return

    repo = LotteryRepository(session)
    lottery = await repo.get_active()

    if lottery is None:
        text = "🎟 <b>Лотерея</b>\n\nЛотерея пока не запущена. Ожидайте объявления!"
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=lottery_menu_kb(False))
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=lottery_menu_kb(False))
        await callback.answer()
        return

    user_tickets = await repo.user_ticket_count(lottery.id, db_user.user_id)
    can_buy = (
        db_user.stars_balance >= lottery.ticket_price
        and (lottery.ref_required == 0 or db_user.referrals_count >= lottery.ref_required)
        and (lottery.ticket_limit == 0 or user_tickets < lottery.ticket_limit)
    )
    text = _lottery_text(lottery, user_tickets, float(db_user.stars_balance))
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=lottery_menu_kb(can_buy, float(lottery.ticket_price)))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=lottery_menu_kb(can_buy, float(lottery.ticket_price)))
    await callback.answer()


@router.callback_query(lambda c: c.data == "game:lottery_buy")
async def cb_lottery_buy(callback: CallbackQuery, session: AsyncSession, db_user: User, bot: Bot) -> None:
    min_refs = max(
        0,
        await SettingsRepository(session).get_int("lottery_min_refs", 3),
    )
    if db_user.referrals_count < min_refs:
        await callback.answer(
            f"❌ Нужно минимум {min_refs} реферала.",
            show_alert=True,
        )
        return
    repo = LotteryRepository(session)
    lottery = await repo.get_active()
    if not lottery:
        await callback.answer("❌ Лотерея не активна.", show_alert=True)
        return

    if lottery.ref_required > 0 and db_user.referrals_count < lottery.ref_required:
        await callback.answer(f"❌ Нужно {lottery.ref_required} рефералов.", show_alert=True)
        return
    if db_user.stars_balance < lottery.ticket_price:
        await callback.answer(f"❌ Нужно {float(lottery.ticket_price):.0f} ⭐", show_alert=True)
        return

    user_tickets = await repo.user_ticket_count(lottery.id, db_user.user_id)
    if lottery.ticket_limit > 0 and user_tickets >= lottery.ticket_limit:
        await callback.answer(f"❌ Лимит {lottery.ticket_limit} билетов.", show_alert=True)
        return

    if lottery.channel_id:
        chat_id = telegram_chat_id(lottery.channel_id)
        if chat_id is None:
            await callback.answer(
                "⚠️ Канал лотереи настроен неверно. Сообщите администратору.",
                show_alert=True,
            )
            return
        try:
            member = await bot.get_chat_member(chat_id, db_user.user_id)
            if not is_subscribed(member):
                await callback.answer(f"❌ Подпишитесь на {lottery.channel_id}", show_alert=True)
                return
        except Exception:
            await callback.answer(
                "⚠️ Не удалось проверить подписку на канал. Попробуйте ещё раз позже.",
                show_alert=True,
            )
            return

    db_user.stars_balance -= lottery.ticket_price
    if not await repo.buy_ticket(lottery, db_user.user_id):
        await callback.answer(
            "Лотерея уже изменилась. Обновите её и попробуйте снова.",
            show_alert=True,
        )
        return

    await callback.answer(f"✅ Билет куплен! (-{float(lottery.ticket_price):.0f} ⭐)")

    if await repo.check_auto_draw(lottery) and lottery.tickets_sold > 0:
        winner_id = await repo.draw_random(lottery)
        if winner_id:
            u_repo = UserRepository(session)
            winner = await u_repo.get(winner_id)
            if winner:
                winner.stars_balance += lottery.prize_pool
            if not await repo.finish(lottery, winner_id):
                await callback.message.answer(
                    "Розыгрыш уже выполняется. Обновите лотерею."
                )
                return
            try:
                await bot.send_message(winner_id, f"🎉 <b>Вы выиграли лотерею!</b>\n🏆 Приз: <b>{float(lottery.prize_pool):.2f} ⭐</b>", parse_mode="HTML")
            except Exception:
                pass
            try:
                await callback.message.edit_text("🎉 <b>Розыгрыш состоялся!</b>\n\nПобедитель уже получил уведомление!", parse_mode="HTML", reply_markup=lottery_menu_kb(False))
            except Exception:
                pass
            return

    user_tickets = await repo.user_ticket_count(lottery.id, db_user.user_id)
    can_buy = (db_user.stars_balance >= lottery.ticket_price and (lottery.ticket_limit == 0 or user_tickets < lottery.ticket_limit))
    try:
        await callback.message.edit_text(
            "✅ <b>Билет куплен!</b>\n\n" + _lottery_text(lottery, user_tickets, float(db_user.stars_balance)),
            parse_mode="HTML", reply_markup=lottery_menu_kb(can_buy, float(lottery.ticket_price)),
        )
    except Exception:
        pass
