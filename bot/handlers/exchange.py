import logging
from decimal import Decimal

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.rp_purchase import RpPurchaseRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.main import back_to_menu_kb
from bot.services.chat_eligibility import credit_stars
from bot.states.exchange import ExchangeStates

router = Router()
logger = logging.getLogger(__name__)

# Preset Telegram-Stars amounts offered on the exchange screen — a manual
# "✏️ Своя сумма" option is also always available.
_AMOUNTS = [10, 25, 50, 100, 250, 500]
_MIN_AMOUNT = 1
_MAX_AMOUNT = 100_000

_PAYLOAD_PREFIX = "rp_buy:"


def _exchange_amounts_kb(amounts: list[int]) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(amounts), 3):
        rows.append([
            InlineKeyboardButton(text=f"{a} ⭐", callback_data=f"exchange:amount:{a}")
            for a in amounts[i:i + 3]
        ])
    rows.append([InlineKeyboardButton(text="✏️ Своя сумма", callback_data="exchange:custom")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _exchange_confirm_kb(amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Купить", callback_data=f"exchange:confirm:{amount}", style="success"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:exchange", style="danger"),
    ]])


def _custom_amount_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:exchange"),
    ]])


def _valid_amount(amount: int) -> bool:
    return _MIN_AMOUNT <= amount <= _MAX_AMOUNT


async def _render_confirm(session: AsyncSession, amount: int) -> str:
    rate = await SettingsRepository(session).get_float("rp_exchange_rate", 1.0)
    rp_amount = (Decimal(str(amount)) * Decimal(str(rate))).quantize(Decimal("0.01"))
    return (
        f"💫 Стоимость: <b>{amount} ⭐</b>\n"
        f"🔄 Будет начислено: <b>{rp_amount} RP⭐️</b>\n\n"
        "Подтверди покупку:"
    )


@router.callback_query(lambda c: c.data == "menu:exchange")
async def cb_exchange_menu(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    rate = await SettingsRepository(session).get_float("rp_exchange_rate", 1.0)
    text = (
        "🪙 <b>Донат</b>\n\n"
        f"Курс: 1 Telegram ⭐ = {rate:g} RP⭐️\n\n"
        "Выбери сумму пополнения в Telegram Stars:"
    )
    kb = _exchange_amounts_kb(_AMOUNTS)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(lambda c: c.data == "exchange:custom")
async def cb_exchange_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ExchangeStates.enter_amount)
    text = f"✏️ Введи количество Telegram Stars (от {_MIN_AMOUNT} до {_MAX_AMOUNT}):"
    try:
        await callback.message.edit_text(text, reply_markup=_custom_amount_cancel_kb())
    except Exception:
        await callback.message.answer(text, reply_markup=_custom_amount_cancel_kb())
    await callback.answer()


@router.message(ExchangeStates.enter_amount)
async def msg_exchange_custom_amount(message: Message, session: AsyncSession, state: FSMContext) -> None:
    try:
        amount = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Введи целое число:", reply_markup=_custom_amount_cancel_kb())
        return
    if not _valid_amount(amount):
        await message.answer(
            f"❌ Сумма должна быть от {_MIN_AMOUNT} до {_MAX_AMOUNT}:",
            reply_markup=_custom_amount_cancel_kb(),
        )
        return

    await state.clear()
    text = await _render_confirm(session, amount)
    await message.answer(text, parse_mode="HTML", reply_markup=_exchange_confirm_kb(amount))


@router.callback_query(lambda c: c.data and c.data.startswith("exchange:amount:"))
async def cb_exchange_amount(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        amount = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    if amount not in _AMOUNTS:
        await callback.answer("❌ Неверная сумма.", show_alert=True)
        return

    text = await _render_confirm(session, amount)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_exchange_confirm_kb(amount))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=_exchange_confirm_kb(amount))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("exchange:confirm:"))
async def cb_exchange_confirm(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    try:
        amount = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    if not _valid_amount(amount):
        await callback.answer("❌ Неверная сумма.", show_alert=True)
        return

    # The rate is resolved fresh here and baked into the invoice payload —
    # this is the only moment that matters for "старые покупки
    # пересчитывать нельзя": whatever rate is live right now is what this
    # specific purchase locks in, regardless of any later admin change.
    rate = await SettingsRepository(session).get_float("rp_exchange_rate", 1.0)
    rp_amount = (Decimal(str(amount)) * Decimal(str(rate))).quantize(Decimal("0.01"))

    await callback.answer()
    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title="Покупка RP⭐️",
            description=f"{amount} Telegram Stars -> {rp_amount} RP⭐️ (курс {rate:g})",
            payload=f"{_PAYLOAD_PREFIX}{callback.from_user.id}:{rp_amount}:{rate}",
            currency="XTR",
            prices=[LabeledPrice(label=f"{rp_amount} RP⭐️", amount=amount)],
        )
    except Exception:
        logger.warning("Failed to send RP purchase invoice to uid=%s", callback.from_user.id, exc_info=True)
        try:
            await callback.message.answer("⚠️ Не удалось создать счёт на оплату. Попробуйте позже.")
        except Exception:
            pass


@router.pre_checkout_query(lambda q: q.invoice_payload.startswith(_PAYLOAD_PREFIX))
async def process_rp_purchase_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(lambda m: m.successful_payment and m.successful_payment.invoice_payload.startswith(_PAYLOAD_PREFIX))
async def msg_rp_purchase_paid(message: Message, db_user: User, session: AsyncSession) -> None:
    payment = message.successful_payment
    payload = payment.invoice_payload
    try:
        _, user_id_str, rp_amount_str, rate_str = payload.split(":")
        payload_user_id = int(user_id_str)
        rp_amount = Decimal(rp_amount_str)
        rate = Decimal(rate_str)
    except (ValueError, IndexError):
        logger.warning("Malformed RP purchase payload: %s", payload)
        return
    if payload_user_id != db_user.user_id:
        logger.warning(
            "RP purchase payload user mismatch: payload=%s actual_uid=%s", payload, db_user.user_id,
        )
        return

    repo = RpPurchaseRepository(session)
    purchase = await repo.record(
        user_id=db_user.user_id,
        stars_paid=payment.total_amount,
        rp_credited=rp_amount,
        rate_at_purchase=rate,
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
    )
    if purchase is None:
        # Telegram redelivered an update for a payment we already recorded
        # and credited — must not credit RP⭐️ a second time.
        await message.answer("✅ Этот платёж уже был обработан ранее.")
        return

    await credit_stars(session, db_user.user_id, rp_amount)
    await message.answer(
        f"✅ <b>Покупка успешна!</b>\n\nНачислено: <b>{rp_amount} RP⭐️</b>",
        parse_mode="HTML",
        reply_markup=back_to_menu_kb(),
    )
