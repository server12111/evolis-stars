from html import escape

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.blocked_sponsor import BlockedSponsorRepository
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.blocked_sponsors import (
    blocked_sponsor_cancel_kb,
    blocked_sponsor_delete_confirm_kb,
    blocked_sponsor_scope_kb,
    blocked_sponsors_list_kb,
)
from bot.services.sponsor_waves import extract_host
from bot.states.admin import AdminBlockedSponsorStates

router = Router()

_MAX_URL_LENGTH = 500


async def _show_list(message: Message, session: AsyncSession) -> None:
    entries = await BlockedSponsorRepository(session).all()
    text = (
        "🚫 <b>Заблокированные спонсоры</b>\n\n"
        f"Всего: <b>{len(entries)}</b>\n\n"
        "🔗 — заблокирована конкретная ссылка. 🌐 — заблокирован весь домен "
        "(полезно для спонсоров с одноразовыми подписанными ссылками, "
        "например FlyerHub-ссылки вида .../search?sign=...).\n\n"
        "Ссылки/домены в этом списке никогда не попадут в стену ОП ни от "
        "одного провайдера (Traffy/TgRass/Botohub/FlyerHub)."
    )
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=blocked_sponsors_list_kb(entries))
    except Exception:
        await message.answer(text, parse_mode="HTML", reply_markup=blocked_sponsors_list_kb(entries))


@router.callback_query(lambda c: c.data == "admin:blocked_sponsors")
async def cb_blocked_sponsors(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    await _show_list(callback.message, session)
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:blocked_sponsor_new")
async def cb_blocked_sponsor_new(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.set_state(AdminBlockedSponsorStates.enter_url)
    await callback.message.answer(
        "🔗 Пришли ту же ссылку, которую бот показывает пользователю на кнопке "
        "«Подписаться» для этого спонсора:",
        reply_markup=blocked_sponsor_cancel_kb(),
    )
    await callback.answer()


@router.message(AdminBlockedSponsorStates.enter_url)
async def msg_blocked_sponsor_url(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    url = (message.text or "").strip()
    if not url or len(url) > _MAX_URL_LENGTH:
        await message.answer(
            "❌ Пришли одну ссылку (не длиннее 500 символов):",
            reply_markup=blocked_sponsor_cancel_kb(),
        )
        return

    host = extract_host(url)
    await state.update_data(url=url)
    await state.set_state(AdminBlockedSponsorStates.choose_scope)
    text = (
        f"🔗 {escape(url)}\n\n"
        "Что заблокировать?\n\n"
        "Если у спонсора каждый раз новая ссылка с меняющимся токеном "
        "(например <code>?sign=...</code>) — точная ссылка не сработает "
        "второй раз, блокируй весь домен."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=blocked_sponsor_scope_kb(host))


@router.callback_query(
    AdminBlockedSponsorStates.choose_scope,
    lambda c: c.data and c.data.startswith("admin:blocked_sponsor_scope:"),
)
async def cb_blocked_sponsor_scope(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    match_type = callback.data.split(":")[2]
    if match_type not in ("url", "domain"):
        await callback.answer()
        return

    data = await state.get_data()
    url = data.get("url", "")
    await state.clear()
    if not url:
        await callback.answer("❌ Устарело, начни заново.", show_alert=True)
        return

    entry = await BlockedSponsorRepository(session).create(url, match_type=match_type)
    if match_type == "domain":
        confirmation = f"✅ <b>Домен заблокирован</b>\n\n🌐 {escape(entry.url_key)}"
    else:
        confirmation = f"✅ <b>Заблокировано</b>\n\n🔗 {escape(entry.url)}"
    await callback.message.answer(
        f"{confirmation}\n\nБольше не будет показываться в стене ОП ни от одного провайдера.",
        parse_mode="HTML",
    )
    await callback.answer()
    await _show_list(callback.message, session)


@router.callback_query(lambda c: c.data and c.data.startswith("admin:blocked_sponsor_del_confirm:"))
async def cb_blocked_sponsor_del_confirm(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    entry_id = int(callback.data.split(":")[2])
    deleted = await BlockedSponsorRepository(session).delete(entry_id)
    await callback.answer("✅ Разблокировано" if deleted else "❌ Не найдено", show_alert=True)
    await _show_list(callback.message, session)


@router.callback_query(lambda c: c.data and c.data.startswith("admin:blocked_sponsor_del:"))
async def cb_blocked_sponsor_del(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    entry_id = int(callback.data.split(":")[2])
    entry = await BlockedSponsorRepository(session).get(entry_id)
    if not entry:
        await callback.answer("❌ Не найдено.", show_alert=True)
        return
    scope_label = f"домен {entry.url_key}" if entry.match_type == "domain" else entry.url
    await callback.message.answer(
        f"🗑 Убрать из блок-листа?\n\n🔗 {escape(scope_label)}\n\n"
        "Спонсор сможет снова появляться в стене ОП, если провайдер продолжает его предлагать.",
        reply_markup=blocked_sponsor_delete_confirm_kb(entry_id),
    )
    await callback.answer()
