import math
import re
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
    amounts_str = await repo.get("withdraw_min_amounts", "15,25,50,100")
    try:
        amounts = sorted({
            int(x.strip())
            for x in amounts_str.split(",")
            if x.strip() and int(x.strip()) > 0
        })
    except ValueError:
        amounts = [15, 25, 50, 100]
    minimum = await repo.get_float("withdraw_min", 15.0)
    amounts = [amount for amount in amounts if amount >= minimum]

    if not amounts:
        text = (
            "⭐ <b>Вывод средств</b>\n\n"
            "Сейчас нет доступных сумм для вывода. Попробуйте позже."
        )
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=back_to_menu_kb(),
            )
        except Exception:
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=back_to_menu_kb(),
            )
        await callback.answer()
        return

    c_repo = ContentRepository(session)
    template = await c_repo.get_text("withdraw")
    balance_str = f"{float(db_user.stars_balance):.2f}"
    try:
        text = template.format(balance=balance_str) if "{" in template else template
    except (KeyError, ValueError, IndexError):
        text = DEFAULT_TEXTS["withdraw"].format(balance=balance_str)
    text += "\n\nВыбери сумму для вывода:"
    photo = await c_repo.get_photo("withdraw")
    kb = withdraw_amounts_kb(amounts)
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
    try:
        allowed_amounts = {
            int(value.strip())
            for value in (await repo.get("withdraw_min_amounts", "15,25,50,100")).split(",")
            if value.strip()
        }
    except ValueError:
        allowed_amounts = {15, 25, 50, 100}
    minimum = await repo.get_float("withdraw_min", 15.0)
    if amount <= 0 or amount not in allowed_amounts or amount < minimum:
        await callback.answer("❌ Неверная сумма.", show_alert=True)
        return

    if db_user.stars_balance < amount:
        await callback.answer(
            f"❌ Недостаточно звёзд. Баланс: {float(db_user.stars_balance):.2f} ⭐",
            show_alert=True,
        )
        return

    await state.set_state(WithdrawStates.choose_recipient)
    await state.update_data(amount=amount)

    text = f"💫 Сумма: <b>{amount} ⭐</b>\n\nКому вывести звёзды?"
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

    await state.set_state(WithdrawStates.confirm)
    await state.update_data(recipient_username=db_user.username)
    text = f"💫 Сумма вывода: <b>{amount} ⭐</b>\n👤 Получатель: @{escape(db_user.username)}"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=withdraw_confirm_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=withdraw_confirm_kb())
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

    await state.set_state(WithdrawStates.confirm)
    await state.update_data(recipient_username=username)
    text = f"💫 Сумма вывода: <b>{amount} ⭐</b>\n🎁 Получатель: @{escape(username)}"
    await message.answer(text, parse_mode="HTML", reply_markup=withdraw_confirm_kb())


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
        f"Сумма: <b>{amount} ⭐</b>\n\n"
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
    try:
        allowed_amounts = {
            int(value.strip())
            for value in (await s_repo.get("withdraw_min_amounts", "15,25,50,100")).split(",")
            if value.strip()
        }
    except ValueError:
        allowed_amounts = {15, 25, 50, 100}
    minimum = await s_repo.get_float("withdraw_min", 15.0)
    if (
        not isinstance(amount, (int, float))
        or not math.isfinite(amount)
        or amount <= 0
        or amount not in allowed_amounts
        or amount < minimum
        or not recipient_username
    ):
        await state.clear()
        await message.answer("❌ Параметры вывода изменились. Выберите сумму заново.", reply_markup=back_to_menu_kb())
        return

    if db_user.stars_balance < amount:
        await state.clear()
        await message.answer("❌ Недостаточно звёзд.", reply_markup=back_to_menu_kb())
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

    # Deduct stars and create withdrawal in the same transaction.
    db_user.stars_balance = round(float(db_user.stars_balance) - amount, 2)
    w_repo = WithdrawalRepository(session)
    withdrawal = await w_repo.create(db_user.user_id, float(amount), recipient_username)

    username_display = f"@{escape(recipient_username)}"
    gift_note = (
        f" (заявитель: ID <code>{db_user.user_id}</code>)"
        if db_user.username != recipient_username
        else ""
    )

    vip_badge = " 💎 VIP" if db_user.is_vip else ""
    request_text = (
        f"📌 <b>Запрос на вывод #{withdrawal.id}</b>{vip_badge}\n\n"
        f"👤 Получатель: {username_display}{gift_note} | ID: <code>{db_user.user_id}</code>\n"
        f"💫 Сумма: <b>{amount} ⭐</b>\n"
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
        f"Сумма: <b>{amount} ⭐</b>\n"
        f"Ожидайте рассмотрения администратором.",
        parse_mode="HTML",
        reply_markup=kb,
    )
