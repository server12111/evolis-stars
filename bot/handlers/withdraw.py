import math
from html import escape

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.withdrawal import WithdrawalRepository
from bot.keyboards.main import back_to_menu_kb
from bot.keyboards.withdraw import (
    admin_withdraw_kb,
    payments_channel_kb,
    withdraw_amounts_kb,
    withdraw_captcha_kb,
)
from bot.services.captcha import generate_captcha
from bot.states.withdraw import WithdrawStates

router = Router()
settings = get_settings()


@router.callback_query(lambda c: c.data == "menu:withdraw")
async def cb_withdraw_menu(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    repo = SettingsRepository(session)

    enabled = await repo.get_bool("withdraw_enabled", True)
    if not enabled:
        await callback.answer("💸 Вывод временно недоступен.", show_alert=True)
        return

    if not db_user.username:
        try:
            await callback.message.edit_text(
                "⚠️ <b>Вывод недоступен</b>\n\n"
                "Для вывода средств необходимо установить Username в Telegram.",
                parse_mode="HTML",
                reply_markup=back_to_menu_kb(),
            )
        except Exception:
            await callback.message.answer(
                "⚠️ <b>Вывод недоступен</b>\n\n"
                "Для вывода средств необходимо установить Username в Telegram.",
                parse_mode="HTML",
                reply_markup=back_to_menu_kb(),
            )
        await callback.answer()
        return

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

    text = (
        f"⭐ <b>Вывод средств</b>\n\n"
        f"💰 Твой баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>\n\n"
        f"Выбери сумму для вывода:"
    )
    photo = await ContentRepository(session).get_photo("withdraw")
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
        await callback.answer("Неверная сумма.", show_alert=True)
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
        await callback.answer("Неверная сумма.", show_alert=True)
        return

    if db_user.stars_balance < amount:
        await callback.answer(
            f"❌ Недостаточно звёзд. Баланс: {float(db_user.stars_balance):.2f} ⭐",
            show_alert=True,
        )
        return

    question, answer = generate_captcha()
    await state.set_state(WithdrawStates.enter_captcha)
    await state.update_data(amount=amount, captcha_answer=answer)

    try:
        await callback.message.edit_text(
            f"🔐 <b>Подтверждение вывода</b>\n\n"
            f"Сумма: <b>{amount} ⭐</b>\n\n"
            f"Решите пример для подтверждения:\n\n"
            f"<b>{question} = ?</b>",
            parse_mode="HTML",
            reply_markup=withdraw_captcha_kb(),
        )
    except Exception:
        await callback.message.answer(
            f"🔐 <b>Подтверждение вывода</b>\n\n"
            f"Сумма: <b>{amount} ⭐</b>\n\n"
            f"Решите пример:\n<b>{question} = ?</b>",
            parse_mode="HTML",
            reply_markup=withdraw_captcha_kb(),
        )
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
        or not db_user.username
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
    withdrawal = await w_repo.create(db_user.user_id, float(amount))

    username_display = (
        f"@{db_user.username}" if db_user.username else escape(db_user.first_name)
    )

    request_text = (
        f"📌 <b>Запрос на вывод #{withdrawal.id}</b>\n\n"
        f"👤 Пользователь: {username_display} | ID: <code>{db_user.user_id}</code>\n"
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
