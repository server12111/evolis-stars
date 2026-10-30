import html
import logging

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.user import UserRepository
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.gift import gift_cancel_kb, gift_confirm_kb, gift_list_kb
from bot.keyboards.admin.main import back_to_admin_kb
from bot.states.admin import AdminGiftStates

router = Router()
logger = logging.getLogger(__name__)

_DESCRIPTION_MAX_LEN = 128


@router.callback_query(lambda c: c.data == "admin:gift")
async def cb_admin_gift(callback: CallbackQuery, db_user: User, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌️ Нет доступа.", show_alert=True)
        return
    await state.clear()
    try:
        gifts = (await bot.get_available_gifts()).gifts
    except Exception as exc:
        await callback.answer(f"❌️ Не удалось получить список подарков: {exc}", show_alert=True)
        return
    if not gifts:
        await callback.answer("❌️ Подарки недоступны.", show_alert=True)
        return
    await state.update_data(gifts_map={g.id: g.star_count for g in gifts})
    text = "🎁 <b>Отправить подарок</b>\n\nВыбери подарок:"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=gift_list_kb(gifts))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=gift_list_kb(gifts))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:gift_pick:"))
async def cb_gift_pick(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌️ Нет доступа.", show_alert=True)
        return
    gift_id = callback.data.split(":", 2)[2]
    data = await state.get_data()
    star_count = (data.get("gifts_map") or {}).get(gift_id)
    if star_count is None:
        await callback.answer("❌️ Список подарков устарел, открой заново.", show_alert=True)
        return
    await state.update_data(gift_id=gift_id, star_count=star_count)
    await state.set_state(AdminGiftStates.enter_description)
    await callback.message.answer(
        f"🎁 Подарок: <b>{star_count}⭐️</b>\n\n"
        f"✏️ Введи текст, который получатель увидит вместе с подарком (до {_DESCRIPTION_MAX_LEN} символов), "
        f"или отправь «-», чтобы отправить без текста:",
        parse_mode="HTML",
        reply_markup=gift_cancel_kb(),
    )
    await callback.answer()


@router.message(AdminGiftStates.enter_description)
async def msg_gift_description(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user):
        return
    raw = (message.text or "").strip()
    description = "" if raw == "-" else raw
    if len(description) > _DESCRIPTION_MAX_LEN:
        await message.answer(
            f"❌️ Слишком длинный текст ({len(description)}/{_DESCRIPTION_MAX_LEN}). Введи короче:",
            reply_markup=gift_cancel_kb(),
        )
        return
    await state.update_data(description=description)
    await state.set_state(AdminGiftStates.enter_target)
    await message.answer(
        "👤 Введи @username или numeric ID получателя:",
        reply_markup=gift_cancel_kb(),
    )


@router.message(AdminGiftStates.enter_target)
async def msg_gift_target(message: Message, state: FSMContext, session: AsyncSession, db_user: User, bot: Bot) -> None:
    if not _is_admin(db_user):
        return
    query = (message.text or "").strip()
    u_repo = UserRepository(session)

    target_id: int | None = None
    target_username: str | None = None
    if query.startswith("@"):
        target = await u_repo.find_by_username(query[1:])
        if target:
            target_id = target.user_id
            target_username = target.username
        else:
            try:
                chat = await bot.get_chat(query)
                if chat.type != "private":
                    raise ValueError("not a user")
                target_id = chat.id
                target_username = chat.username
            except Exception:
                pass
    else:
        try:
            target_id = int(query)
            if target_id <= 0:
                # Gifts go to users only -- a negative ID is a chat/channel.
                target_id = None
                raise ValueError
            target = await u_repo.get(target_id)
            target_username = target.username if target else None
        except ValueError:
            target = await u_repo.find_by_username(query)
            if target:
                target_id = target.user_id
                target_username = target.username

    if target_id is None:
        await message.answer(
            "❌️ Пользователь не найден. Введи @username или numeric ID ещё раз:",
            reply_markup=gift_cancel_kb(),
        )
        return

    await state.update_data(target_id=target_id, target_username=target_username)
    await state.set_state(AdminGiftStates.confirm)
    data = await state.get_data()
    recipient_display = f"@{html.escape(target_username)}" if target_username else f"<code>{target_id}</code>"
    description = data.get("description") or "—"
    await message.answer(
        f"🎁 <b>Подтверди отправку подарка</b>\n\n"
        f"Подарок: <b>{data['star_count']}⭐️</b>\n"
        f"Получатель: {recipient_display} (ID <code>{target_id}</code>)\n"
        f"Текст: {html.escape(description) if description != '—' else description}\n\n"
        f"⚠️ Списывается со звёздного баланса бота.",
        parse_mode="HTML",
        reply_markup=gift_confirm_kb(),
    )


@router.callback_query(AdminGiftStates.confirm, lambda c: c.data == "admin:gift_send")
async def cb_gift_send(callback: CallbackQuery, db_user: User, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌️ Нет доступа.", show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    description = data.get("description") or None
    try:
        await bot.send_gift(
            gift_id=data["gift_id"],
            user_id=data["target_id"],
            text=description,
        )
    except Exception as exc:
        logger.warning("Failed to send gift %s to %s: %s", data.get("gift_id"), data.get("target_id"), exc)
        try:
            await callback.message.edit_text(
                f"❌️ Не удалось отправить подарок: {exc}",
                reply_markup=back_to_admin_kb(),
            )
        except Exception:
            await callback.message.answer(
                f"❌️ Не удалось отправить подарок: {exc}",
                reply_markup=back_to_admin_kb(),
            )
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            f"✔️ Подарок <b>{data['star_count']}⭐️</b> отправлен пользователю <code>{data['target_id']}</code>.",
            parse_mode="HTML",
            reply_markup=back_to_admin_kb(),
        )
    except Exception:
        await callback.message.answer(
            f"✔️ Подарок отправлен пользователю <code>{data['target_id']}</code>.",
            parse_mode="HTML",
            reply_markup=back_to_admin_kb(),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:gift_cancel")
async def cb_gift_cancel(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌️ Нет доступа.", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_text("🎁 Отправка подарка отменена.", reply_markup=back_to_admin_kb())
    except Exception:
        await callback.message.answer("🎁 Отправка подарка отменена.", reply_markup=back_to_admin_kb())
    await callback.answer()
