import html
import json
import logging
import math

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import case, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User, Withdrawal
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository
from bot.database.repositories.withdrawal import WithdrawalRepository
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.main import back_to_admin_kb
from bot.keyboards.admin.users import cancel_kb, user_actions_kb, users_menu_kb
from bot.states.admin import AdminUserStates

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)


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
    username_display = (
        f"@{user.username}"
        if user and user.username
        else html.escape(user.first_name if user else str(withdrawal.user_id))
    )
    text = (
        f"📌 <b>Запрос на вывод #{withdrawal.id}</b>\n\n"
        f"👤 Пользователь: {username_display} | ID: <code>{withdrawal.user_id}</code>\n"
        f"💫 Сумма: <b>{float(withdrawal.amount):.0f} ⭐</b>\n"
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


@router.callback_query(lambda c: c.data == "admin:users")
async def cb_users(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_text(
            "👥 <b>Управление пользователями</b>\n\nНайди пользователя по ID или Username:",
            parse_mode="HTML",
            reply_markup=users_menu_kb(),
        )
    except Exception:
        await callback.message.answer("👥 Управление пользователями", reply_markup=users_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:user_search")
async def cb_user_search(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminUserStates.search)
    try:
        await callback.message.edit_text(
            "🔍 Введи ID или @username пользователя:",
            reply_markup=cancel_kb("admin:users"),
        )
    except Exception:
        await callback.message.answer("🔍 Введи ID или @username:", reply_markup=cancel_kb("admin:users"))
    await callback.answer()


def _sponsor_wave_lines(target: User) -> list[str]:
    lines: list[str] = []
    for raw in (target.sponsor_wave_one, target.sponsor_wave_two):
        if not raw:
            continue
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("url"):
                lines.append(f"• {item.get('provider', '?')}: {item.get('url')}")
    return lines


@router.message(AdminUserStates.search)
async def msg_user_search(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user):
        return
    await state.clear()
    query = (message.text or "").strip()
    u_repo = UserRepository(session)

    target = None
    if query.startswith("@"):
        target = await u_repo.find_by_username(query[1:])
    else:
        try:
            target = await u_repo.get(int(query))
        except ValueError:
            target = await u_repo.find_by_username(query)

    if not target:
        await message.answer("❌ Пользователь не найден.", reply_markup=back_to_admin_kb())
        return

    username_display = f"@{target.username}" if target.username else "нет"
    text = (
        f"👤 <b>Пользователь</b>\n\n"
        f"ID: <code>{target.user_id}</code>\n"
        f"Имя: <b>{html.escape(target.first_name)}</b>\n"
        f"Username: {username_display}\n"
        f"💰 Баланс: <b>{float(target.stars_balance):.2f} ⭐</b>\n"
        f"👥 Рефералов: <b>{target.referrals_count}</b>\n"
        f"📋 Заданий: <b>{target.tasks_completed_count}</b>\n"
        f"🚫 Заблокирован: {'Да' if target.is_blocked else 'Нет'}"
    )

    if target.referrer_id:
        if target.referral_reward_given:
            referral_status = "✅ выплачена"
        elif target.referral_insufficient_notified:
            referral_status = "⚠️ недостаточно спонсоров"
        elif not target.sponsors_verified:
            referral_status = "⏳ спонсоры ещё не подтверждены"
        else:
            referral_status = "⏳ в процессе"
        referrer_line = str(target.referrer_id)
    else:
        referrer_line = "нет"
        referral_status = "—"

    wave_lines = _sponsor_wave_lines(target)
    wave_display = "\n".join(wave_lines) if wave_lines else "нет"

    text += (
        f"\n\n🔗 Реферер: <code>{referrer_line}</code>\n"
        f"🎯 Реф. награда: {referral_status}\n"
        f"🛡 Спонсоры подтверждены: {'Да' if target.sponsors_verified else 'Нет'}\n"
        f"📡 Волна: {target.sponsor_wave}\n"
        f"📋 Закреплённые спонсоры:\n{wave_display}"
    )

    await message.answer(text, parse_mode="HTML", reply_markup=user_actions_kb(target.user_id, target.is_blocked))


async def _get_target_and_check(callback: CallbackQuery, session: AsyncSession, db_user: User, user_id: int) -> User | None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return None
    u_repo = UserRepository(session)
    target = await u_repo.get(user_id)
    if not target:
        await callback.answer("❌ Пользователь не найден.", show_alert=True)
    return target


@router.callback_query(lambda c: c.data and c.data.startswith("admin:user_block:"))
async def cb_user_block(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    uid = int(callback.data.split(":")[2])
    target = await _get_target_and_check(callback, session, db_user, uid)
    if not target: return
    target.is_blocked = True
    await session.commit()
    await callback.answer("✅ Пользователь заблокирован.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=user_actions_kb(uid, True))
    except Exception:
        pass


@router.callback_query(lambda c: c.data and c.data.startswith("admin:user_unblock:"))
async def cb_user_unblock(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    uid = int(callback.data.split(":")[2])
    target = await _get_target_and_check(callback, session, db_user, uid)
    if not target: return
    target.is_blocked = False
    await session.commit()
    await callback.answer("✅ Пользователь разблокирован.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=user_actions_kb(uid, False))
    except Exception:
        pass


@router.callback_query(lambda c: c.data and c.data.startswith("admin:user_add:"))
async def cb_user_add_stars(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    uid = int(callback.data.split(":")[2])
    await state.set_state(AdminUserStates.add_stars)
    await state.update_data(target_id=uid)
    await callback.message.answer(f"➕ Введи кол-во ⭐ для начисления пользователю {uid}:", reply_markup=cancel_kb("admin:users"))
    await callback.answer()


@router.message(AdminUserStates.add_stars)
async def msg_add_stars(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    data = await state.get_data()
    await state.clear()
    try:
        amount = float(message.text.strip().replace(",", "."))
        if not math.isfinite(amount) or amount <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число.")
        return
    u_repo = UserRepository(session)
    target = await u_repo.get(data["target_id"])
    if not target:
        await message.answer("❌ Пользователь не найден.")
        return
    await session.execute(
        update(User).where(User.user_id == target.user_id).values(stars_balance=User.stars_balance + amount)
    )
    await session.commit()
    await session.refresh(target)
    await message.answer(f"✅ Начислено <b>+{amount:.2f} ⭐</b> пользователю <code>{target.user_id}</code>. Баланс: <b>{float(target.stars_balance):.2f} ⭐</b>", parse_mode="HTML", reply_markup=back_to_admin_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("admin:user_sub:"))
async def cb_user_sub_stars(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    uid = int(callback.data.split(":")[2])
    await state.set_state(AdminUserStates.sub_stars)
    await state.update_data(target_id=uid)
    await callback.message.answer(f"➖ Введи кол-во ⭐ для списания у пользователя {uid}:", reply_markup=cancel_kb("admin:users"))
    await callback.answer()


@router.message(AdminUserStates.sub_stars)
async def msg_sub_stars(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    data = await state.get_data()
    await state.clear()
    try:
        amount = float(message.text.strip().replace(",", "."))
        if not math.isfinite(amount) or amount <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число.")
        return
    u_repo = UserRepository(session)
    target = await u_repo.get(data["target_id"])
    if not target:
        await message.answer("❌ Пользователь не найден.")
        return
    await session.execute(
        update(User).where(User.user_id == target.user_id).values(
            stars_balance=case(
                (User.stars_balance >= amount, User.stars_balance - amount),
                else_=0,
            )
        )
    )
    await session.commit()
    await session.refresh(target)
    await message.answer(f"✅ Списано <b>-{amount:.2f} ⭐</b> у пользователя <code>{target.user_id}</code>. Баланс: <b>{float(target.stars_balance):.2f} ⭐</b>", parse_mode="HTML", reply_markup=back_to_admin_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("admin:user_refs:"))
async def cb_user_add_refs(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    uid = int(callback.data.split(":")[2])
    await state.set_state(AdminUserStates.add_refs)
    await state.update_data(target_id=uid)
    await callback.message.answer(f"👥 Введи кол-во рефералов для начисления пользователю {uid}:", reply_markup=cancel_kb("admin:users"))
    await callback.answer()


@router.message(AdminUserStates.add_refs)
async def msg_add_refs(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    data = await state.get_data()
    await state.clear()
    try:
        amount = int(message.text.strip())
        if amount <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи целое положительное число.")
        return
    u_repo = UserRepository(session)
    target = await u_repo.get(data["target_id"])
    if not target:
        await message.answer("❌ Пользователь не найден.")
        return
    target.referrals_count += amount
    await session.commit()
    await message.answer(f"✅ Начислено <b>+{amount}</b> рефералов. Итого: <b>{target.referrals_count}</b>", parse_mode="HTML", reply_markup=back_to_admin_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("admin:withdraw_approve:"))
async def cb_withdraw_approve(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user):
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
            f"✅ <b>Заявка #{w.id} одобрена!</b>\n\nСумма: <b>{float(w.amount):.0f} ⭐</b>\nСкоро вы получите выплату.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _update_public_withdrawal_status(callback, session, w, "Принято", "✅")
    await callback.answer("✅ Одобрено")


@router.callback_query(lambda c: c.data and c.data.startswith("admin:withdraw_reject:"))
async def cb_withdraw_reject(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    wid = int(callback.data.split(":")[2])
    w_repo = WithdrawalRepository(session)
    w = await w_repo.reject(wid)
    if not w:
        await callback.answer("❌ Заявка уже обработана.", show_alert=True)
        return

    # Refund
    await session.execute(
        update(User).where(User.user_id == w.user_id).values(stars_balance=User.stars_balance + w.amount)
    )
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
            f"❌ <b>Заявка #{w.id} отклонена.</b>\n\nСумма <b>{float(w.amount):.0f} ⭐</b> возвращена на баланс.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _update_public_withdrawal_status(callback, session, w, "Отклонено", "❌")
    await callback.answer("❌ Отклонено")
