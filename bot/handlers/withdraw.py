import re
from decimal import Decimal
from html import escape

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.content import DEFAULT_TEXTS, ContentRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.withdrawal import WithdrawalRepository
from bot.keyboards.main import back_to_menu_kb
from bot.keyboards.withdraw import (
    admin_withdraw_kb,
    payments_channel_kb,
    withdraw_amounts_kb,
    withdraw_captcha_kb,
    withdraw_confirm_kb,
    withdraw_method_kb,
    withdraw_recipient_cancel_kb,
    withdraw_recipient_choice_kb,
)
from bot.services.captcha import generate_captcha
from bot.states.withdraw import WithdrawStates

router = Router()
settings = get_settings()

# Telegram username rules: letters/digits/underscores, 5-32 characters
# total, must start with a letter.
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")

# The only withdrawal amounts offered, in Telegram ⭐ — no free-text entry,
# no other values. Fixed by design (see spec): the user never enters an
# arbitrary amount.
_WITHDRAW_AMOUNTS = [15, 25, 50, 100]
# Fixed conversion for withdrawals specifically — independent of the
# purchase-side rp_exchange_rate admin setting.
_RP_PER_STAR = 3

# Amounts >= this ask how the user wants to receive Stars; smaller amounts
# keep the original single-screen flow with a default method.
_METHOD_CHOICE_THRESHOLD = 50
_DEFAULT_METHOD = "fragment"
_METHOD_LABELS_USER = {"fragment": "Через Fragment", "gift": "Подарком"}
_METHOD_LABELS_ADMIN = {"fragment": "Fragment", "gift": "Подарок"}


def _rp_cost(amount: int) -> Decimal:
    return Decimal(amount) * _RP_PER_STAR


def _confirm_text(amount: int, recipient_username: str, emoji: str, method: str | None = None) -> str:
    lines = [
        f"{emoji} Получатель: @{escape(recipient_username)}",
        "",
        f"Будет списано: <b>{_rp_cost(amount)} RP⭐️</b>",
        f"Пользователь получит: <b>{amount} Telegram ⭐</b>",
    ]
    if method:
        lines.append(f"Способ: {_METHOD_LABELS_USER[method]}")
    return "\n".join(lines)


async def _advance_after_recipient(state: FSMContext, amount: int) -> bool:
    """Returns True if the method-choice screen should be shown (caller
    renders it); False if the caller should render its own confirm screen
    (state is already `confirm`, method already defaulted)."""
    if amount >= _METHOD_CHOICE_THRESHOLD:
        await state.set_state(WithdrawStates.choose_method)
        return True
    await state.set_state(WithdrawStates.confirm)
    await state.update_data(withdrawal_method=_DEFAULT_METHOD)
    return False


def _normalize_username(raw: str) -> str | None:
    """Strip an optional leading '@' and whitespace, then validate against
    Telegram's username format. Returns None if the result isn't a valid
    username."""
    candidate = raw.strip()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    if not _USERNAME_RE.fullmatch(candidate):
        return None
    return candidate


@router.callback_query(lambda c: c.data == "menu:withdraw")
async def cb_withdraw_menu(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    repo = SettingsRepository(session)

    enabled = await repo.get_bool("withdraw_enabled", True)
    if not enabled:
        await callback.answer("💸 Вывод временно недоступен.", show_alert=True)
        return

    # A Telegram username is only required for the "себе" (self) recipient
    # mode, checked there — "другому пользователю" needs no username of
    # the requester's own, so this is no longer an upfront gate.
    c_repo = ContentRepository(session)
    template = await c_repo.get_text("withdraw")
    balance_str = f"{float(db_user.stars_balance):.2f}"
    try:
        text = template.format(balance=balance_str) if "{" in template else template
    except (KeyError, ValueError, IndexError):
        text = DEFAULT_TEXTS["withdraw"].format(balance=balance_str)
    text += "\n\nВыбери сумму для вывода (в Telegram ⭐):"
    photo = await c_repo.get_photo("withdraw")
    kb = withdraw_amounts_kb(_WITHDRAW_AMOUNTS)
    if photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("withdraw:amount:"))
async def cb_withdraw_amount(
    callback: CallbackQuery,
    db_user: User,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        amount = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверная сумма.", show_alert=True)
        return

    repo = SettingsRepository(session)
    if not await repo.get_bool("withdraw_enabled", True):
        await callback.answer("💸 Вывод временно недоступен.", show_alert=True)
        return
    if amount not in _WITHDRAW_AMOUNTS:
        await callback.answer("❌ Неверная сумма.", show_alert=True)
        return

    rp_needed = _rp_cost(amount)
    if db_user.stars_balance < rp_needed:
        await callback.answer(
            f"❌ Недостаточно RP⭐️. Нужно {rp_needed} RP⭐️, баланс: {float(db_user.stars_balance):.2f} RP⭐️",
            show_alert=True,
        )
        return

    await state.set_state(WithdrawStates.choose_recipient)
    await state.update_data(amount=amount)

    text = (
        f"💫 Получите: <b>{amount} Telegram ⭐</b>\n"
        f"🔄 Спишется: <b>{rp_needed} RP⭐️</b>\n\n"
        f"Кому вывести?"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=withdraw_recipient_choice_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=withdraw_recipient_choice_kb())
    await callback.answer()


@router.callback_query(WithdrawStates.choose_recipient, lambda c: c.data == "withdraw:recipient:self")
async def cb_withdraw_recipient_self(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    amount = data.get("amount")
    if amount is None:
        await callback.answer()
        return

    if not db_user.username:
        text = (
            "⚠️ Для вывода себе нужен Telegram username.\n\n"
            "Установи его в настройках Telegram или выбери «Другому пользователю»."
        )
        try:
            await callback.message.edit_text(text, reply_markup=withdraw_recipient_choice_kb())
        except Exception:
            await callback.message.answer(text, reply_markup=withdraw_recipient_choice_kb())
        await callback.answer()
        return

    await state.update_data(recipient_username=db_user.username)
    if await _advance_after_recipient(state, amount):
        text = "Каким способом вывести?"
        kb = withdraw_method_kb()
    else:
        text = _confirm_text(amount, db_user.username, "👤")
        kb = withdraw_confirm_kb()
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(WithdrawStates.choose_recipient, lambda c: c.data == "withdraw:recipient:other")
async def cb_withdraw_recipient_other(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WithdrawStates.enter_recipient_username)
    text = "✏️ Отправь Telegram username получателя (с @ или без):"
    try:
        await callback.message.edit_text(text, reply_markup=withdraw_recipient_cancel_kb())
    except Exception:
        await callback.message.answer(text, reply_markup=withdraw_recipient_cancel_kb())
    await callback.answer()


@router.message(WithdrawStates.enter_recipient_username)
async def msg_recipient_username(message: Message, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    amount = data.get("amount")
    if amount is None:
        await state.clear()
        return

    username = _normalize_username(message.text or "")
    if not username:
        await message.answer(
            "❌ Некорректный username. Введи его ещё раз (с @ или без):",
            reply_markup=withdraw_recipient_cancel_kb(),
        )
        return

    if db_user.username and username.lower() == db_user.username.lower():
        await message.answer(
            "❌ Это твой собственный username — для вывода себе используй «Себе». "
            "Введи другой username или отмени:",
            reply_markup=withdraw_recipient_cancel_kb(),
        )
        return

    await state.update_data(recipient_username=username)
    if await _advance_after_recipient(state, amount):
        text = "Каким способом вывести?"
        kb = withdraw_method_kb()
    else:
        text = _confirm_text(amount, username, "🎁")
        kb = withdraw_confirm_kb()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(WithdrawStates.choose_method, lambda c: c.data and c.data.startswith("withdraw:method:"))
async def cb_withdraw_method(callback: CallbackQuery, state: FSMContext) -> None:
    method = callback.data.split(":", 2)[2]
    if method not in _METHOD_LABELS_USER:
        await callback.answer()
        return

    data = await state.get_data()
    amount = data.get("amount")
    recipient_username = data.get("recipient_username")
    if amount is None or not recipient_username:
        await callback.answer()
        return

    await state.set_state(WithdrawStates.confirm)
    await state.update_data(withdrawal_method=method)

    text = _confirm_text(amount, recipient_username, "👤", method)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=withdraw_confirm_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=withdraw_confirm_kb())
    await callback.answer()


@router.callback_query(WithdrawStates.confirm, lambda c: c.data == "withdraw:confirm")
async def cb_withdraw_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    amount = data.get("amount")
    recipient_username = data.get("recipient_username")
    if amount is None or not recipient_username:
        await callback.answer()
        return

    question, answer = generate_captcha()
    await state.set_state(WithdrawStates.enter_captcha)
    await state.update_data(captcha_answer=answer)

    text = (
        f"🔐 <b>Подтверждение вывода</b>\n\n"
        f"Списание: <b>{_rp_cost(amount)} RP⭐️</b> → получите <b>{amount} Telegram ⭐</b>\n\n"
        f"Решите пример для подтверждения:\n\n"
        f"<b>{question} = ?</b>"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=withdraw_captcha_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=withdraw_captcha_kb())
    await callback.answer()


@router.message(WithdrawStates.enter_captcha)
async def msg_captcha(
    message: Message,
    db_user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    correct = data.get("captcha_answer")
    amount = data.get("amount", 0)
    recipient_username = data.get("recipient_username")
    withdrawal_method = data.get("withdrawal_method") or _DEFAULT_METHOD
    if withdrawal_method not in _METHOD_LABELS_ADMIN:
        withdrawal_method = _DEFAULT_METHOD

    try:
        user_answer = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer("❌ Введи число:", reply_markup=withdraw_captcha_kb())
        return

    if user_answer != correct:
        await message.answer("❌ Неверный ответ. Попробуй ещё раз:", reply_markup=withdraw_captcha_kb())
        return

    s_repo = SettingsRepository(session)
    if not await s_repo.get_bool("withdraw_enabled", True):
        await state.clear()
        await message.answer("💸 Вывод временно недоступен.", reply_markup=back_to_menu_kb())
        return
    if (
        not isinstance(amount, int)
        or amount not in _WITHDRAW_AMOUNTS
        or not recipient_username
    ):
        await state.clear()
        await message.answer("❌ Параметры вывода изменились. Выберите сумму заново.", reply_markup=back_to_menu_kb())
        return

    rp_needed = _rp_cost(amount)
    if db_user.stars_balance < rp_needed:
        await state.clear()
        await message.answer("❌ Недостаточно RP⭐️.", reply_markup=back_to_menu_kb())
        return

    payments_channel_id = settings.payments_channel_id or await s_repo.get("payments_channel_id")
    payments_link = settings.payments_channel_link or await s_repo.get("payments_channel_link")
    admin_channel_id = settings.admin_channel_id or await s_repo.get("admin_channel_id")
    if not admin_channel_id:
        await state.clear()
        await message.answer(
            "⚠️ Вывод временно недоступен: канал обработки заявок не настроен.",
            reply_markup=back_to_menu_kb(),
        )
        return

    await state.clear()

    # Deduct RP⭐️ and create withdrawal in the same transaction.
    db_user.stars_balance = round(float(db_user.stars_balance) - float(rp_needed), 2)
    w_repo = WithdrawalRepository(session)
    withdrawal = await w_repo.create(
        db_user.user_id, float(amount), recipient_username, withdrawal_method, rp_needed,
    )

    username_display = f"@{escape(recipient_username)}"
    gift_note = (
        f" (заявитель: ID <code>{db_user.user_id}</code>)"
        if db_user.username != recipient_username
        else ""
    )

    vip_badge = " 💎 VIP" if db_user.is_vip else ""
    # Method is only ever genuinely chosen for amount >= _METHOD_CHOICE_THRESHOLD
    # (see _advance_after_recipient) — showing it for a smaller withdrawal
    # would misleadingly imply the user picked something they were never asked.
    method_line = (
        f"🔧 Способ вывода: <b>{_METHOD_LABELS_ADMIN[withdrawal_method]}</b>\n"
        if amount >= _METHOD_CHOICE_THRESHOLD else ""
    )
    request_text = (
        f"📌 <b>Новая заявка на вывод #{withdrawal.id}</b>{vip_badge}\n\n"
        f"👤 Пользователь: @{escape(db_user.username) if db_user.username else db_user.user_id} | ID: <code>{db_user.user_id}</code>\n"
        f"🎁 Получатель: {username_display}{gift_note}\n"
        f"💫 Получит: <b>{amount} Telegram ⭐</b>\n"
        f"{method_line}"
        f"⏳ Статус: На рассмотрении"
    )

    import logging as _log
    _logger = _log.getLogger(__name__)

    # Send to public payments channel
    ch_msg_id = None
    if payments_channel_id:
        try:
            msg = await bot.send_message(int(payments_channel_id), request_text, parse_mode="HTML")
            ch_msg_id = msg.message_id
        except Exception as e:
            _logger.warning("Cannot send to payments channel %s: %s", payments_channel_id, e)

    # Send to admin channel with approve/reject buttons
    adm_msg_id = None
    if admin_channel_id:
        try:
            msg = await bot.send_message(
                int(admin_channel_id),
                request_text,
                parse_mode="HTML",
                reply_markup=admin_withdraw_kb(withdrawal.id),
            )
            adm_msg_id = msg.message_id
        except Exception as e:
            _logger.warning("Cannot send to admin channel %s: %s", admin_channel_id, e)
            await session.rollback()
            if ch_msg_id and payments_channel_id:
                try:
                    await bot.delete_message(
                        int(payments_channel_id),
                        ch_msg_id,
                    )
                except Exception:
                    pass
            await message.answer(
                "⚠️ Не удалось передать заявку администраторам. "
                "Звёзды не списаны, попробуйте позже.",
                reply_markup=back_to_menu_kb(),
            )
            return

    withdrawal.channel_message_id = ch_msg_id
    withdrawal.admin_message_id = adm_msg_id
    await session.commit()

    kb = payments_channel_kb(payments_link) if payments_link else back_to_menu_kb()
    await message.answer(
        f"✅ <b>Заявка #{withdrawal.id} создана!</b>\n\n"
        f"Получите: <b>{amount} Telegram ⭐</b>\n"
        f"Ожидайте рассмотрения администратором.",
        parse_mode="HTML",
        reply_markup=kb,
    )
