import logging
import re
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.crypto_withdrawal import CryptoWithdrawalRepository
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.withdraw import _normalize_username
from bot.keyboards.crypto_withdraw import (
    admin_crypto_withdraw_kb,
    crypto_method_choice_kb,
    crypto_withdraw_amounts_kb,
    crypto_withdraw_cancel_kb,
    crypto_withdraw_confirm_kb,
)
from bot.keyboards.main import back_to_menu_kb
from bot.keyboards.withdraw import payments_channel_kb
from bot.services.chat_eligibility import credit_stars, debit_stars_if_enough
from bot.services.ton_price import get_ton_usd_rate
from bot.services.withdrawal_numbering import next_withdrawal_number
from bot.states.crypto_withdraw import CryptoWithdrawStates

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)

_CRYPTO_QUICK_AMOUNTS = [50, 100, 250, 500]
_CRYPTO_MIN_DEFAULT = 50

# Standard user-friendly (base64url) TON wallet address length -- format
# sanity check only, no checksum verification, same "compile a regex,
# validate in a helper" precedent as withdraw.py's _USERNAME_RE.
_TON_ADDRESS_RE = re.compile(r"^[A-Za-z0-9_-]{48}$")

_METHOD_LABELS = {"usdt_send": "USDT (через @send)", "ton": "TON"}


def _parse_rp_amount(raw: str) -> int | None:
    # \xa0 -- the non-breaking space Telegram clients insert as a thousands
    # separator (e.g. copy-pasting "1 000") -- must be stripped separately
    # from a plain ASCII space, same as _parse_vc_amount.
    cleaned = raw.strip().replace(" ", "").replace("\xa0", "").replace(",", "")
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _normalize_ton_address(raw: str) -> str | None:
    candidate = raw.strip()
    if not _TON_ADDRESS_RE.fullmatch(candidate):
        return None
    return candidate


async def _reply(inner: CallbackQuery | Message, text: str, *, reply_markup=None) -> None:
    if hasattr(inner, "message") and inner.message is not None:
        try:
            await inner.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            await inner.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        await inner.answer()
    else:
        await inner.answer(text, parse_mode="HTML", reply_markup=reply_markup)


def _fmt_payout(method: str, amount: Decimal) -> str:
    if method == "usdt_send":
        return f"{amount:.2f} $"
    return f"{amount:.4f} TON"


@router.callback_query(lambda c: c.data == "withdraw:choose:crypto")
async def cb_withdraw_crypto_menu(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    repo = SettingsRepository(session)
    if not await repo.get_bool("withdraw_crypto_enabled", True):
        await callback.answer("🪙 Вывод крипты временно недоступен.", show_alert=True)
        return

    crypto_min = await repo.get_int("crypto_min_withdrawal", _CRYPTO_MIN_DEFAULT)
    rate = await repo.get_float("crypto_rp_usd_rate", 0.005)
    text = (
        f"🪙 <b>Вывод крипты</b>\n\n"
        f"Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>\n"
        f"Курс: 1 RP⭐️ = {rate:g}$\n"
        f"Мин. вывод: <b>{crypto_min}</b> RP⭐️\n\n"
        f"💵 <b>USDT через @send</b> — популярный Telegram-бот для отправки крипты, "
        f"выплата придёт на ваш Telegram username.\n"
        f"💎 <b>TON</b> — сумма в TON по текущему курсу, выплата на кошелёк.\n\n"
        f"Выберите способ:"
    )
    await _reply(callback, text, reply_markup=crypto_method_choice_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("cryptowithdraw:method:"))
async def cb_crypto_method(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    method = callback.data.split(":")[2]
    if method not in _METHOD_LABELS:
        await callback.answer("❌ Неверный способ.", show_alert=True)
        return

    repo = SettingsRepository(session)
    if not await repo.get_bool("withdraw_crypto_enabled", True):
        await callback.answer("🪙 Вывод крипты временно недоступен.", show_alert=True)
        return

    crypto_min = await repo.get_int("crypto_min_withdrawal", _CRYPTO_MIN_DEFAULT)
    await state.set_state(CryptoWithdrawStates.enter_amount)
    await state.update_data(crypto_method=method)
    quick = [a for a in _CRYPTO_QUICK_AMOUNTS if a >= crypto_min]
    text = (
        f"🪙 <b>Вывод {_METHOD_LABELS[method]}</b>\n\n"
        f"Мин. вывод: <b>{crypto_min}</b> RP⭐️\n\n"
        f"Выберите сумму RP⭐️ для вывода:"
    )
    await _reply(callback, text, reply_markup=crypto_withdraw_amounts_kb(quick))


async def _process_crypto_amount(
    inner: CallbackQuery | Message,
    db_user: User,
    session: AsyncSession,
    state: FSMContext,
    amount: int,
) -> None:
    data = await state.get_data()
    method = data.get("crypto_method")
    if method not in _METHOD_LABELS:
        await _reply(inner, "❌ Начните заново: «Задания» → «Вывод» → «Крипта».", reply_markup=back_to_menu_kb())
        return

    repo = SettingsRepository(session)
    if not await repo.get_bool("withdraw_crypto_enabled", True):
        await _reply(inner, "🪙 Вывод крипты временно недоступен.", reply_markup=back_to_menu_kb())
        return

    crypto_min = await repo.get_int("crypto_min_withdrawal", _CRYPTO_MIN_DEFAULT)
    if amount < crypto_min:
        await _reply(
            inner,
            f"❌ Сумма должна быть от {crypto_min} RP⭐️. Попробуй ещё раз:",
            reply_markup=crypto_withdraw_cancel_kb(),
        )
        return
    if Decimal(amount) > db_user.stars_balance:
        await _reply(
            inner,
            f"❌ Недостаточно RP⭐️. Баланс: {float(db_user.stars_balance):.2f} RP⭐️",
            reply_markup=crypto_withdraw_cancel_kb(),
        )
        return

    rp_usd_rate = Decimal(str(await repo.get_float("crypto_rp_usd_rate", 0.005)))
    usd_value = Decimal(amount) * rp_usd_rate
    ton_rate: Decimal | None = None
    if method == "ton":
        raw_rate = await get_ton_usd_rate()
        if raw_rate is None:
            await _reply(
                inner,
                "⚠️ Курс TON временно недоступен. Попробуйте позже.",
                reply_markup=crypto_withdraw_cancel_kb(),
            )
            return
        ton_rate = Decimal(str(raw_rate))
        payout_amount = usd_value / ton_rate
    else:
        payout_amount = usd_value

    await state.set_state(CryptoWithdrawStates.enter_recipient)
    await state.update_data(
        crypto_rp_amount=amount,
        crypto_payout_amount=str(payout_amount),
        crypto_ton_rate=str(ton_rate) if ton_rate is not None else "",
    )
    if method == "usdt_send":
        prompt = "✏️ Введите ваш Telegram username (без @) — на него пришлют выплату через @send:"
    else:
        prompt = "✏️ Введите адрес TON-кошелька для выплаты:"
    await _reply(inner, prompt, reply_markup=crypto_withdraw_cancel_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("cryptowithdraw:amount:"))
async def cb_crypto_amount(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    raw = callback.data.split(":")[2]
    if raw == "custom":
        await _reply(callback, "✏️ Введите сумму RP⭐️ для вывода:", reply_markup=crypto_withdraw_cancel_kb())
        return
    try:
        amount = int(raw)
    except ValueError:
        await callback.answer("❌ Неверная сумма.", show_alert=True)
        return
    await _process_crypto_amount(callback, db_user, session, state, amount)


@router.message(CryptoWithdrawStates.enter_amount)
async def msg_crypto_amount_custom(message: Message, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    amount = _parse_rp_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введи целое число RP⭐️:", reply_markup=crypto_withdraw_cancel_kb())
        return
    await _process_crypto_amount(message, db_user, session, state, amount)


@router.message(CryptoWithdrawStates.enter_recipient)
async def msg_crypto_recipient(message: Message, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    method = data.get("crypto_method")
    if method not in _METHOD_LABELS or "crypto_rp_amount" not in data:
        await message.answer("❌ Начните заново: «Задания» → «Вывод» → «Крипта».", reply_markup=back_to_menu_kb())
        await state.clear()
        return

    raw = message.text or ""
    if method == "usdt_send":
        recipient = _normalize_username(raw)
        if recipient is None:
            await message.answer(
                "❌ Неверный username. Введите Telegram username (5-32 символа, латиница/цифры):",
                reply_markup=crypto_withdraw_cancel_kb(),
            )
            return
    else:
        recipient = _normalize_ton_address(raw)
        if recipient is None:
            await message.answer(
                "❌ Неверный адрес кошелька. Проверьте и введите ещё раз:",
                reply_markup=crypto_withdraw_cancel_kb(),
            )
            return

    amount = int(data["crypto_rp_amount"])
    payout_amount = Decimal(data["crypto_payout_amount"])
    ton_rate_raw = data.get("crypto_ton_rate") or ""

    lines = [
        f"🪙 <b>Вывод {_METHOD_LABELS[method]}</b>\n",
        f"Спишется: <b>{amount} RP⭐️</b>",
        f"Вам пришлют: <b>{_fmt_payout(method, payout_amount)}</b>",
    ]
    if method == "usdt_send":
        lines.append(f"Кому: @{recipient} (через @send)")
    else:
        lines.append(f"Курс: 1 TON = {float(Decimal(ton_rate_raw)):.4f}$")
        lines.append(f"Кошелёк: <code>{recipient}</code>")
        lines.append("Сумма TON зафиксирована на момент заявки.")
    lines.append("\nПодтвердить?")

    await state.set_state(CryptoWithdrawStates.confirm)
    await state.update_data(crypto_recipient=recipient)
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=crypto_withdraw_confirm_kb())


@router.callback_query(CryptoWithdrawStates.confirm, lambda c: c.data == "cryptowithdraw:confirm")
async def cb_crypto_confirm(
    callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext, bot: Bot,
) -> None:
    await _finalize_crypto_withdrawal(callback, db_user, session, state, bot)


async def _finalize_crypto_withdrawal(
    callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext, bot: Bot,
) -> None:
    data = await state.get_data()
    await state.clear()
    method = data.get("crypto_method")
    recipient = data.get("crypto_recipient")
    try:
        amount = int(data.get("crypto_rp_amount"))
        payout_amount = Decimal(data.get("crypto_payout_amount", "0"))
        ton_rate_raw = data.get("crypto_ton_rate") or ""
        ton_rate = Decimal(ton_rate_raw) if ton_rate_raw else None
    except (TypeError, ValueError, InvalidOperation):
        await callback.answer("❌ Параметры вывода устарели. Начните заново.", show_alert=True)
        return
    if method not in _METHOD_LABELS or not recipient or amount <= 0 or payout_amount <= 0:
        await callback.answer("❌ Параметры вывода устарели. Начните заново.", show_alert=True)
        return

    repo = SettingsRepository(session)
    if not await repo.get_bool("withdraw_crypto_enabled", True):
        await callback.message.answer("🪙 Вывод крипты временно недоступен.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    # Re-check against *current* min bound, not just the one shown when the
    # amount was picked -- an admin may have raised it while this user sat
    # on the confirm screen.
    crypto_min = await repo.get_int("crypto_min_withdrawal", _CRYPTO_MIN_DEFAULT)
    if amount < crypto_min:
        await callback.message.answer(
            f"❌ Сумма должна быть от {crypto_min} RP⭐️. Начните заново.",
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
    # async session. Same pattern as vc_withdraw.py.
    user_id = db_user.user_id
    rp_cost = Decimal(amount)

    if not await debit_stars_if_enough(session, user_id, rp_cost):
        await callback.message.answer("❌ Недостаточно RP⭐️.", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    w_repo = CryptoWithdrawalRepository(session)
    withdrawal = await w_repo.create(user_id, method, rp_cost, payout_amount, ton_rate, recipient)
    withdrawal.display_number = await next_withdrawal_number(session)

    vip_badge = " 💎 VIP" if db_user.is_vip else ""
    user_display = f"@{escape(db_user.username)}" if db_user.username else str(db_user.user_id)
    payout_line = f"🪙 Получит: <b>{_fmt_payout(method, payout_amount)}</b> ({_METHOD_LABELS[method]})"

    # Public payments-channel post must NOT include the recipient (wallet
    # address or username) — only the admin post does, since admin is the
    # one who actually has to use it to send the payout.
    public_text = (
        f"🪙 <b>Новая заявка на вывод крипты #{withdrawal.display_number}</b>{vip_badge}\n\n"
        f"👤 Пользователь: {user_display} | ID: <code>{db_user.user_id}</code>\n"
        f"{payout_line}\n"
        f"💰 Списано: <b>{amount} RP⭐️</b>\n"
        f"⏳ Статус: На рассмотрении"
    )
    admin_text = (
        f"🪙 <b>Новая заявка на вывод крипты #{withdrawal.display_number}</b>{vip_badge}\n\n"
        f"👤 Пользователь: {user_display} | ID: <code>{db_user.user_id}</code>\n"
        f"{payout_line}\n"
        f"💰 Списано: <b>{amount} RP⭐️</b>\n"
        f"📤 Кому отправить: <code>{recipient}</code>\n"
        f"⏳ Статус: На рассмотрении"
    )

    ch_msg_id = None
    if payments_channel_id:
        try:
            msg = await bot.send_message(int(payments_channel_id), public_text, parse_mode="HTML")
            ch_msg_id = msg.message_id
        except Exception as e:
            logger.warning("Cannot send crypto withdrawal to payments channel %s: %s", payments_channel_id, e)

    adm_msg_id = None
    try:
        msg = await bot.send_message(
            int(admin_channel_id),
            admin_text,
            parse_mode="HTML",
            reply_markup=admin_crypto_withdraw_kb(withdrawal.id),
        )
        adm_msg_id = msg.message_id
    except Exception as e:
        logger.warning("Cannot send crypto withdrawal to admin channel %s: %s", admin_channel_id, e)
        # debit_stars_if_enough above is its own already-committed
        # transaction — rollback() only discards the still-pending
        # CryptoWithdrawal row, not that. Must be refunded explicitly or
        # the RP⭐️ vanishes with no withdrawal ever created for it.
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
        f"Вам пришлют: <b>{_fmt_payout(method, payout_amount)}</b>\n"
        f"Ожидайте обработки.",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await callback.answer()
