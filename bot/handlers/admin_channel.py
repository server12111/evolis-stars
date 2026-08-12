import html
import logging

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import CryptoWithdrawal, VcWithdrawal, Withdrawal
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_broadcast import ChatBroadcastRepository
from bot.database.repositories.crypto_withdrawal import CryptoWithdrawalRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository
from bot.database.repositories.vc_withdrawal import VcWithdrawalRepository
from bot.database.repositories.withdrawal import WithdrawalRepository
from bot.handlers.crypto_withdraw import _METHOD_LABELS as _CRYPTO_METHOD_LABELS
from bot.handlers.crypto_withdraw import _fmt_payout as _fmt_crypto_payout_amount

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)

_METHOD_LABELS_ADMIN = {"fragment": "Fragment", "gift": "Подарок"}
# Must match withdraw.py's _METHOD_CHOICE_THRESHOLD — method is only ever
# genuinely chosen for amount >= this, so it's only shown here in that case.
_METHOD_CHOICE_THRESHOLD = 50


def _fmt_crypto_payout(withdrawal: CryptoWithdrawal) -> str:
    return _fmt_crypto_payout_amount(withdrawal.method, withdrawal.payout_amount)


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_list


async def _update_public_withdrawal_status(
    callback: CallbackQuery,
    session: AsyncSession,
    withdrawal: Withdrawal,
    status: str,
    status_icon: str,
) -> None:
    if not withdrawal.channel_message_id:
        return

    payments_channel_id = (
        settings.payments_channel_id
        or await SettingsRepository(session).get("payments_channel_id")
    )
    if not payments_channel_id:
        return

    user = await UserRepository(session).get(withdrawal.user_id)
    # recipient_username is who Stars actually go to (may differ from the
    # requester for a gift withdrawal) — nullable only for rows created
    # before this field existed, which always meant "the requester's own".
    recipient_username = withdrawal.recipient_username or (user.username if user else None)
    username_display = (
        f"@{recipient_username}"
        if recipient_username
        else html.escape(user.first_name if user else str(withdrawal.user_id))
    )
    vip_badge = " 💎 VIP" if user and user.is_vip else ""
    method_line = ""
    if float(withdrawal.amount) >= _METHOD_CHOICE_THRESHOLD:
        method_label = _METHOD_LABELS_ADMIN.get(withdrawal.withdrawal_method or "fragment", "Fragment")
        method_line = f"🔧 Способ вывода: <b>{method_label}</b>\n"
    text = (
        f"📌 <b>Запрос на вывод #{withdrawal.display_number or withdrawal.id}</b>{vip_badge}\n\n"
        f"👤 Получатель: {username_display} | ID: <code>{withdrawal.user_id}</code>\n"
        f"💫 Получит: <b>{float(withdrawal.amount):.0f} Telegram ⭐</b>\n"
        f"{method_line}"
        f"{status_icon} Статус: <b>{status}</b>"
    )
    try:
        await callback.bot.edit_message_text(
            chat_id=int(payments_channel_id),
            message_id=withdrawal.channel_message_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning(
            "Cannot update withdrawal %s in payments channel %s: %s",
            withdrawal.id,
            payments_channel_id,
            exc,
        )


async def _update_public_vc_withdrawal_status(
    callback: CallbackQuery,
    session: AsyncSession,
    withdrawal: VcWithdrawal,
    status: str,
    status_icon: str,
) -> None:
    if not withdrawal.channel_message_id:
        return

    payments_channel_id = (
        settings.payments_channel_id
        or await SettingsRepository(session).get("payments_channel_id")
    )
    if not payments_channel_id:
        return

    user = await UserRepository(session).get(withdrawal.user_id)
    user_display = f"@{html.escape(user.username)}" if user and user.username else str(withdrawal.user_id)
    vip_badge = " 💎 VIP" if user and user.is_vip else ""
    text = (
        f"💎 <b>Запрос на вывод VC #{withdrawal.display_number or withdrawal.id}</b>{vip_badge}\n\n"
        f"👤 Пользователь: {user_display} | ID: <code>{withdrawal.user_id}</code>\n"
        f"💎 Получит: <b>{float(withdrawal.vc_amount):.0f} VC</b>\n"
        f"{status_icon} Статус: <b>{status}</b>"
    )
    try:
        await callback.bot.edit_message_text(
            chat_id=int(payments_channel_id),
            message_id=withdrawal.channel_message_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning(
            "Cannot update VC withdrawal %s in payments channel %s: %s",
            withdrawal.id,
            payments_channel_id,
            exc,
        )


async def _update_public_crypto_withdrawal_status(
    callback: CallbackQuery,
    session: AsyncSession,
    withdrawal: CryptoWithdrawal,
    status: str,
    status_icon: str,
) -> None:
    if not withdrawal.channel_message_id:
        return

    payments_channel_id = (
        settings.payments_channel_id
        or await SettingsRepository(session).get("payments_channel_id")
    )
    if not payments_channel_id:
        return

    user = await UserRepository(session).get(withdrawal.user_id)
    user_display = f"@{html.escape(user.username)}" if user and user.username else str(withdrawal.user_id)
    vip_badge = " 💎 VIP" if user and user.is_vip else ""
    # Never the recipient (wallet/username) here — this is the PUBLIC post,
    # same rule as when it was first sent (see crypto_withdraw.py's
    # public_text vs admin_text split).
    text = (
        f"🪙 <b>Запрос на вывод крипты #{withdrawal.display_number or withdrawal.id}</b>{vip_badge}\n\n"
        f"👤 Пользователь: {user_display} | ID: <code>{withdrawal.user_id}</code>\n"
        f"🪙 Получит: <b>{_fmt_crypto_payout(withdrawal)}</b> ({_CRYPTO_METHOD_LABELS.get(withdrawal.method, withdrawal.method)})\n"
        f"{status_icon} Статус: <b>{status}</b>"
    )
    try:
        await callback.bot.edit_message_text(
            chat_id=int(payments_channel_id),
            message_id=withdrawal.channel_message_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning(
            "Cannot update crypto withdrawal %s in payments channel %s: %s",
            withdrawal.id,
            payments_channel_id,
            exc,
        )


@router.callback_query(lambda c: c.data and c.data.startswith("admin:withdraw_approve:"))
async def cb_withdraw_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    wid = int(callback.data.split(":")[2])
    w_repo = WithdrawalRepository(session)
    w = await w_repo.approve(wid)
    if not w:
        await callback.answer("❌ Заявка уже обработана.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ <b>Принято</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    try:
        await callback.bot.send_message(
            w.user_id,
            f"✅ <b>Заявка #{w.display_number or w.id} одобрена!</b>\n\nВы получите: <b>{float(w.amount):.0f} Telegram ⭐</b>\nСкоро вы получите выплату.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _update_public_withdrawal_status(callback, session, w, "Принято", "✅")
    await callback.answer("✅ Одобрено")


@router.callback_query(lambda c: c.data and c.data.startswith("admin:withdraw_reject:"))
async def cb_withdraw_reject(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    wid = int(callback.data.split(":")[2])
    w_repo = WithdrawalRepository(session)
    w = await w_repo.reject(wid)
    if not w:
        await callback.answer("❌ Заявка уже обработана.", show_alert=True)
        return
    await session.commit()

    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ <b>Отклонено</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    try:
        rp_debited = w.rp_debited if w.rp_debited is not None else w.amount * 3
        await callback.bot.send_message(
            w.user_id,
            f"❌ <b>Заявка #{w.display_number or w.id} отклонена.</b>\n\n"
            f"Списанные <b>{float(rp_debited):.0f} RP⭐️</b> не возвращены на баланс.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _update_public_withdrawal_status(callback, session, w, "Отклонено", "❌")
    await callback.answer("❌ Отклонено")


@router.callback_query(lambda c: c.data and c.data.startswith("admin:vcwithdraw_approve:"))
async def cb_vcwithdraw_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    wid = int(callback.data.split(":")[2])
    w = await VcWithdrawalRepository(session).approve(wid)
    if not w:
        await callback.answer("❌ Заявка уже обработана.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ <b>Принято</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    try:
        await callback.bot.send_message(
            w.user_id,
            f"✅ <b>Заявка #{w.display_number or w.id} одобрена!</b>\n\nВы получите: <b>{float(w.vc_amount):.0f} VC</b>\nСкоро вы получите выплату.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _update_public_vc_withdrawal_status(callback, session, w, "Принято", "✅")
    await callback.answer("✅ Одобрено")


@router.callback_query(lambda c: c.data and c.data.startswith("admin:vcwithdraw_reject:"))
async def cb_vcwithdraw_reject(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    wid = int(callback.data.split(":")[2])
    w_repo = VcWithdrawalRepository(session)
    w = await w_repo.reject(wid)
    if not w:
        await callback.answer("❌ Заявка уже обработана.", show_alert=True)
        return
    await session.commit()

    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ <b>Отклонено</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    try:
        await callback.bot.send_message(
            w.user_id,
            f"❌ <b>Заявка #{w.display_number or w.id} отклонена.</b>\n\n"
            f"Списанные <b>{float(w.rp_debited):.0f} RP⭐️</b> не возвращены на баланс.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _update_public_vc_withdrawal_status(callback, session, w, "Отклонено", "❌")
    await callback.answer("❌ Отклонено")


@router.callback_query(lambda c: c.data and c.data.startswith("admin:cryptowithdraw_approve:"))
async def cb_cryptowithdraw_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    wid = int(callback.data.split(":")[2])
    w = await CryptoWithdrawalRepository(session).approve(wid)
    if not w:
        await callback.answer("❌ Заявка уже обработана.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ <b>Принято</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    try:
        # Unlike the public channel post, this DM is the user's OWN
        # recipient (their own wallet/username) coming back to them — not
        # exposed to anyone else, so echoing it here is fine and useful as
        # a "sending here" confirmation.
        await callback.bot.send_message(
            w.user_id,
            f"✅ <b>Заявка #{w.display_number or w.id} одобрена!</b>\n\n"
            f"Вы получите: <b>{_fmt_crypto_payout(w)}</b> на <code>{w.recipient}</code>\n"
            f"Скоро вы получите выплату.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _update_public_crypto_withdrawal_status(callback, session, w, "Принято", "✅")
    await callback.answer("✅ Одобрено")


@router.callback_query(lambda c: c.data and c.data.startswith("admin:cryptowithdraw_reject:"))
async def cb_cryptowithdraw_reject(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    wid = int(callback.data.split(":")[2])
    w_repo = CryptoWithdrawalRepository(session)
    w = await w_repo.reject(wid)
    if not w:
        await callback.answer("❌ Заявка уже обработана.", show_alert=True)
        return
    await session.commit()

    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ <b>Отклонено</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    try:
        await callback.bot.send_message(
            w.user_id,
            f"❌ <b>Заявка #{w.display_number or w.id} отклонена.</b>\n\n"
            f"Списанные <b>{float(w.rp_amount):.0f} RP⭐️</b> не возвращены на баланс.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _update_public_crypto_withdrawal_status(callback, session, w, "Отклонено", "❌")
    await callback.answer("❌ Отклонено")


@router.callback_query(lambda c: c.data and c.data.startswith("broadcast_mod:approve:"))
async def cb_broadcast_mod_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    message_id = int(callback.data.split(":")[2])
    broadcast_msg = await ChatBroadcastRepository(session).approve(message_id)
    if not broadcast_msg:
        await callback.answer("❌ Уже обработано.", show_alert=True)
        return

    try:
        await callback.message.edit_text(
            (callback.message.text or "") + "\n\n✅ <b>Одобрено</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    chat = await ChatRepository(session).get(broadcast_msg.chat_id)
    if chat:
        # custom_broadcast_enabled can already be True here if this text
        # replaced a previously-approved one that was already live — in
        # that case approval genuinely resumes sending immediately, not
        # just "queues it up" (the owner still has to flip the toggle
        # themselves for a first-time setup).
        owner_text = (
            "✅ <b>Ваш текст прошёл проверку, рассылка запущена!</b>"
            if chat.custom_broadcast_enabled
            else "✅ <b>Ваш текст прошёл проверку!</b>\n\nВключите рассылку в панели чата, чтобы она заработала."
        )
        try:
            await callback.bot.send_message(chat.owner_user_id, owner_text, parse_mode="HTML")
        except Exception:
            pass
    await callback.answer("✅ Одобрено")


@router.callback_query(lambda c: c.data and c.data.startswith("broadcast_mod:reject:"))
async def cb_broadcast_mod_reject(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    message_id = int(callback.data.split(":")[2])
    result = await ChatBroadcastRepository(session).reject(message_id)
    if not result:
        await callback.answer("❌ Уже обработано.", show_alert=True)
        return
    chat_id, _text = result

    try:
        await callback.message.edit_text(
            (callback.message.text or "") + "\n\n❌ <b>Отклонено</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    chat = await ChatRepository(session).get(chat_id)
    if chat:
        try:
            await callback.bot.send_message(
                chat.owner_user_id,
                "❌ <b>Вашему тексту для рассылки отказано.</b>\n\nПопробуйте отправить другой текст.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    await callback.answer("❌ Отклонено")
