import logging
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.vc_withdrawal import VcWithdrawalRepository
from bot.keyboards.main import back_to_menu_kb
from bot.keyboards.vc_withdraw import (
    admin_vc_withdraw_kb,
    vc_withdraw_amounts_kb,
    vc_withdraw_cancel_kb,
    vc_withdraw_confirm_kb,
    vc_withdraw_subscribe_kb,
)
from bot.keyboards.withdraw import payments_channel_kb
from bot.services.chat_eligibility import credit_stars, debit_stars_if_enough
from bot.services.referral import vip_and_tier_badge
from bot.services.telegram_chat import is_subscribed, telegram_chat_id
from bot.services.withdrawal_numbering import next_withdrawal_number
from bot.states.vc_withdraw import VcWithdrawStates

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)

_VC_QUICK_AMOUNTS = [10_000, 25_000, 50_000, 100_000]
_VC_MIN_DEFAULT = 10_000
_VC_MAX_DEFAULT = 500_000

# (upper bound inclusive, settings key, default rate) -- ranges are
# 10k-25k / 25k-50k / 50k-100k / 100k-300k / 300k-500k VC; a boundary
# value (e.g. exactly 25000) belongs to the LOWER tier since these are
# scanned in ascending order and the first match wins.
_VC_TIERS = [
    (25_000, "vc_rate_tier1", 1000.0),
    (50_000, "vc_rate_tier2", 1250.0),
    (100_000, "vc_rate_tier3", 1500.0),
    (300_000, "vc_rate_tier4", 2000.0),
    (500_000, "vc_rate_tier5", 2500.0),
]


async def _vc_rate_for_amount(session: AsyncSession, vc_amount: int) -> Decimal:
    repo = SettingsRepository(session)
    for upper, key, default in _VC_TIERS:
        if vc_amount <= upper:
            return Decimal(str(await repo.get_float(key, default)))
    # Above the highest boundary shouldn't happen (max withdrawal is
    # enforced before this is ever called) -- fall back to the top tier
    # rather than error.
    _, key, default = _VC_TIERS[-1]
    return Decimal(str(await repo.get_float(key, default)))


def _rp_cost_for_vc(vc_amount: int, rate: Decimal) -> Decimal:
    """RP⭐️ cost for a VC withdrawal, rounded UP to 2 decimals so the
    house never under-charges due to rounding."""
    from decimal import ROUND_CEILING
    return (Decimal(vc_amount) / rate).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _parse_vc_amount(raw: str) -> int | None:
    cleaned = raw.strip().replace(" ", "").replace(" ", "").replace(",", "")
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _fmt_rate(rate: Decimal) -> str:
    return f"{float(rate):g}"


def _confirm_text(vc_amount: int, rate: Decimal, rp_cost: Decimal) -> str:
    return (
        f"💎 <b>Вывод VC</b>\n\n"
        f"Курс: 1 RP⭐️ = {_fmt_rate(rate)} VC\n"
        f"Спишется: <b>{rp_cost} RP⭐️</b>\n"
        f"Вам пришлют: <b>{vc_amount} VC</b>\n\n"
        f"Подтвердить?"
    )


async def _reply(inner: CallbackQuery | Message, text: str, *, reply_markup=None) -> None:
    # Duck-typed on "has a .message" rather than isinstance(inner,
    # CallbackQuery): a CallbackQuery always carries the message it was
    # attached to; a Message never has a nested .message of its own.
    if hasattr(inner, "message") and inner.message is not None:
        try:
            await inner.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            await inner.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        await inner.answer()
    else:
        await inner.answer(text, parse_mode="HTML", reply_markup=reply_markup)


async def _check_vc_subscription(bot: Bot, user_id: int, session: AsyncSession) -> bool:
    """True if subscribed, OR the gate isn't configured/resolvable, OR the
    live check itself fails -- a mandatory-subscribe requirement isn't
    worth blocking a legitimate withdrawal over a transient API hiccup or
    a bad channel setting."""
    link = await SettingsRepository(session).get("vc_mandatory_channel", "")
    if not link:
        return True
    chat_id = telegram_chat_id(link)
    if chat_id is None:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return True
    return is_subscribed(member)


@router.callback_query(lambda c: c.data == "withdraw:choose:vc")
async def cb_withdraw_vc_menu(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    repo = SettingsRepository(session)
    if not await repo.get_bool("withdraw_vc_enabled", True):
        await callback.answer("💎 Вывод VC временно недоступен.", show_alert=True)
        return

    vc_min = await repo.get_int("vc_min_withdrawal", _VC_MIN_DEFAULT)
    vc_max = await repo.get_int("vc_max_withdrawal", _VC_MAX_DEFAULT)
    rates = [await repo.get_float(key, default) for _, key, default in _VC_TIERS]

    text = (
        f"💎 <b>Вывод VC</b>\n\n"
        f"Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>\n\n"
        f"Курсы (1 RP⭐️ = VC):\n"
        f"10к-25к VC: <b>{rates[0]:g}</b>\n"
        f"25к-50к VC: <b>{rates[1]:g}</b>\n"
        f"50к-100к VC: <b>{rates[2]:g}</b>\n"
        f"100к-300к VC: <b>{rates[3]:g}</b>\n"
        f"300к-500к VC: <b>{rates[4]:g}</b>\n\n"
        f"Мин. вывод: <b>{vc_min}</b> VC, макс: <b>{vc_max}</b> VC\n\n"
        f"Выбери сумму VC для вывода:"
    )
    await _reply(callback, text, reply_markup=vc_withdraw_amounts_kb(_VC_QUICK_AMOUNTS))


async def _process_vc_amount(
    inner: CallbackQuery | Message,
    db_user: User,
    session: AsyncSession,
    state: FSMContext,
    amount: int,
) -> None:
    repo = SettingsRepository(session)
    if not await repo.get_bool("withdraw_vc_enabled", True):
        await _reply(inner, "💎 Вывод VC временно недоступен.", reply_markup=back_to_menu_kb())
        return

    vc_min = await repo.get_int("vc_min_withdrawal", _VC_MIN_DEFAULT)
    vc_max = await repo.get_int("vc_max_withdrawal", _VC_MAX_DEFAULT)
    if amount < vc_min or amount > vc_max:
        await _reply(
            inner,
            f"❌ Сумма должна быть от {vc_min} до {vc_max} VC. Попробуй ещё раз:",
            reply_markup=vc_withdraw_cancel_kb(),
        )
        return

    rate = await _vc_rate_for_amount(session, amount)
    rp_cost = _rp_cost_for_vc(amount, rate)
    if db_user.stars_balance < rp_cost:
        await _reply(
            inner,
            f"❌ Недостаточно RP⭐️. Нужно {rp_cost} RP⭐️, баланс: {float(db_user.stars_balance):.2f} RP⭐️",
            reply_markup=vc_withdraw_cancel_kb(),
        )
        return

    await state.set_state(VcWithdrawStates.confirm)
    await state.update_data(vc_amount=amount, vc_rate=str(rate), vc_rp_cost=str(rp_cost))
    await _reply(inner, _confirm_text(amount, rate, rp_cost), reply_markup=vc_withdraw_confirm_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("vcwithdraw:amount:"))
async def cb_vc_amount(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    raw = callback.data.split(":")[2]
    if raw == "custom":
        await state.set_state(VcWithdrawStates.enter_amount)
        await _reply(callback, "✏️ Введите сумму VC для вывода:", reply_markup=vc_withdraw_cancel_kb())
        return
    try:
        amount = int(raw)
    except ValueError:
        await callback.answer("❌ Неверная сумма.", show_alert=True)
        return
    await _process_vc_amount(callback, db_user, session, state, amount)


@router.message(VcWithdrawStates.enter_amount)
async def msg_vc_amount_custom(message: Message, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    amount = _parse_vc_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введи целое число VC:", reply_markup=vc_withdraw_cancel_kb())
        return
    await _process_vc_amount(message, db_user, session, state, amount)


@router.callback_query(VcWithdrawStates.confirm, lambda c: c.data == "vcwithdraw:confirm")
async def cb_vc_confirm(
    callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext, bot: Bot,
) -> None:
    data = await state.get_data()
    if "vc_amount" not in data:
        await callback.answer()
        return

    if not await _check_vc_subscription(bot, db_user.user_id, session):
        link = await SettingsRepository(session).get("vc_mandatory_channel", "")
        await _reply(
            callback,
            f"📢 Для вывода VC подпишитесь на канал, затем нажмите «Я подписался»:\n{escape(link)}",
            reply_markup=vc_withdraw_subscribe_kb(link),
        )
        return

    await _finalize_vc_withdrawal(callback, db_user, session, state, bot)


@router.callback_query(VcWithdrawStates.confirm, lambda c: c.data == "vcwithdraw:check_sub")
async def cb_vc_check_sub(
    callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext, bot: Bot,
) -> None:
    data = await state.get_data()
    if "vc_amount" not in data:
        await callback.answer()
        return
    if not await _check_vc_subscription(bot, db_user.user_id, session):
        await callback.answer("❌ Вы ещё не подписаны. Подпишитесь и попробуйте снова.", show_alert=True)
        return
    await _finalize_vc_withdrawal(callback, db_user, session, state, bot)


async def _finalize_vc_withdrawal(
    callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext, bot: Bot,
) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        amount = int(data.get("vc_amount"))
        rate = Decimal(data.get("vc_rate", "0"))
        rp_cost = Decimal(data.get("vc_rp_cost", "0"))
    except (TypeError, ValueError, InvalidOperation):
        await callback.answer("❌ Параметры вывода устарели. Начните заново.", show_alert=True)
        return
    if amount <= 0 or rate <= 0 or rp_cost <= 0:
        await callback.answer("❌ Параметры вывода устарели. Начните заново.", show_alert=True)
        return

    repo = SettingsRepository(session)
    if not await repo.get_bool("withdraw_vc_enabled", True):
        await callback.message.answer("💎 Вывод VC временно недоступен.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    # Re-check against *current* bounds, not just the ones shown when the
    # amount was picked -- an admin may have tightened min/max while this
    # user sat on the confirm screen.
    vc_min = await repo.get_int("vc_min_withdrawal", _VC_MIN_DEFAULT)
    vc_max = await repo.get_int("vc_max_withdrawal", _VC_MAX_DEFAULT)
    if amount < vc_min or amount > vc_max:
        await callback.message.answer(
            f"❌ Сумма должна быть от {vc_min} до {vc_max} VC. Начните заново.",
            reply_markup=back_to_menu_kb(),
        )
        await callback.answer()
        return

    admin_channel_id = settings.admin_channel_id or await repo.get("admin_channel_id")
    if not admin_channel_id:
        await callback.message.answer(
            "⚠️ Вывод временно недоступен: канал обработки заявок не настроен.",
            reply_markup=back_to_menu_kb(),
        )
        await callback.answer()
        return

    payments_channel_id = settings.payments_channel_id or await repo.get("payments_channel_id")
    payments_link = settings.payments_channel_link or await repo.get("payments_channel_link")

    # Captured now, not re-read off db_user later — a rollback below (if
    # the admin-channel send fails) expires the whole identity map, and
    # accessing an expired attribute outside an active await crashes an
    # async session. Same pattern as withdraw.py's stars flow.
    user_id = db_user.user_id

    if not await debit_stars_if_enough(session, user_id, rp_cost):
        await callback.message.answer("❌ Недостаточно RP⭐️.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    w_repo = VcWithdrawalRepository(session)
    withdrawal = await w_repo.create(user_id, Decimal(amount), rate, rp_cost)
    withdrawal.display_number = await next_withdrawal_number(session)

    vip_badge = vip_and_tier_badge(db_user.is_vip, db_user.referral_tier)
    user_display = f"@{escape(db_user.username)}" if db_user.username else str(db_user.user_id)
    request_text = (
        f"💎 <b>Новая заявка на вывод VC #{withdrawal.display_number}</b>{vip_badge}\n\n"
        f"👤 Пользователь: {user_display} | ID: <code>{db_user.user_id}</code>\n"
        f"💎 Получит: <b>{amount} VC</b>\n"
        f"🔄 Курс: 1 RP⭐️ = {_fmt_rate(rate)} VC\n"
        f"💰 Списано: <b>{rp_cost} RP⭐️</b>\n"
        f"⏳ Статус: На рассмотрении"
    )

    # Send to public payments channel first (mirrors withdraw.py's stars
    # flow) -- same channel, so admins and users see both currencies as one
    # continuous stream instead of VC requests being invisible there.
    ch_msg_id = None
    if payments_channel_id:
        try:
            msg = await bot.send_message(int(payments_channel_id), request_text, parse_mode="HTML")
            ch_msg_id = msg.message_id
        except Exception as e:
            logger.warning("Cannot send VC withdrawal to payments channel %s: %s", payments_channel_id, e)

    adm_msg_id = None
    try:
        msg = await bot.send_message(
            int(admin_channel_id),
            request_text,
            parse_mode="HTML",
            reply_markup=admin_vc_withdraw_kb(withdrawal.id),
        )
        adm_msg_id = msg.message_id
    except Exception as e:
        logger.warning("Cannot send VC withdrawal to admin channel %s: %s", admin_channel_id, e)
        # debit_stars_if_enough above is its own already-committed
        # transaction — rollback() only discards the still-pending
        # VcWithdrawal row, not that. Must be refunded explicitly or the
        # RP⭐️ vanishes with no withdrawal ever created for it.
        await session.rollback()
        await credit_stars(session, user_id, rp_cost)
        if ch_msg_id and payments_channel_id:
            try:
                await bot.delete_message(int(payments_channel_id), ch_msg_id)
            except Exception:
                pass
        await callback.message.answer(
            "⚠️ Не удалось передать заявку администраторам. RP⭐️ не списаны, попробуйте позже.",
            reply_markup=back_to_menu_kb(),
        )
        await callback.answer()
        return

    withdrawal.channel_message_id = ch_msg_id
    withdrawal.admin_message_id = adm_msg_id
    await session.commit()

    kb = payments_channel_kb(payments_link) if payments_link else back_to_menu_kb()
    await callback.message.answer(
        f"✅ <b>Заявка #{withdrawal.display_number} создана!</b>\n\n"
        f"Вам пришлют: <b>{amount} VC</b>\n"
        f"Ожидайте обработки.",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await callback.answer()
