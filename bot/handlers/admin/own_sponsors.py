from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.own_sponsor import OwnSponsorRepository
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.main import back_to_admin_kb
from bot.keyboards.admin.own_sponsors import (
    own_sponsor_cancel_kb,
    own_sponsor_delete_confirm_kb,
    own_sponsor_kind_kb,
    own_sponsor_view_kb,
    own_sponsors_list_kb,
)
from bot.services.telegram_chat import is_bot_admin_in_chat
from bot.states.admin import AdminOwnSponsorStates

router = Router()

_KIND_LABELS = {"channel": "📢 Канал/чат", "bot": "🤖 Бот"}


@router.callback_query(lambda c: c.data == "admin:own_sponsors")
async def cb_own_sponsors(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    repo = OwnSponsorRepository(session)
    sponsors = await repo.all()
    text = f"📢 <b>Свои спонсоры</b>\n\nВсего: <b>{len(sponsors)}</b>"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=own_sponsors_list_kb(sponsors))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=own_sponsors_list_kb(sponsors))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:own_sponsor_view:"))
async def cb_own_sponsor_view(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    sponsor_id = int(callback.data.split(":")[2])
    sponsor = await OwnSponsorRepository(session).get(sponsor_id)
    if not sponsor:
        await callback.answer("❌ Не найдено.", show_alert=True)
        return
    text = (
        f"{_KIND_LABELS.get(sponsor.kind, sponsor.kind)} <b>{sponsor.name}</b>\n\n"
        f"🔗 {sponsor.url}\n"
        + (f"🎯 Цель проверки: <code>{sponsor.target}</code>\n" if sponsor.target else "")
        + f"👥 Набрано: <b>{sponsor.current_count}/{sponsor.target_count}</b>"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML", reply_markup=own_sponsor_view_kb(sponsor.id, sponsor.is_active)
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="HTML", reply_markup=own_sponsor_view_kb(sponsor.id, sponsor.is_active)
        )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:own_sponsor_toggle:"))
async def cb_own_sponsor_toggle(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    sponsor_id = int(callback.data.split(":")[2])
    sponsor = await OwnSponsorRepository(session).toggle(sponsor_id)
    if not sponsor:
        await callback.answer("❌ Не найдено.", show_alert=True)
        return
    status = "включён" if sponsor.is_active else "отключён"
    await callback.answer(f"{'✅' if sponsor.is_active else '⏸'} Спонсор {status}")
    try:
        await callback.message.edit_reply_markup(reply_markup=own_sponsor_view_kb(sponsor.id, sponsor.is_active))
    except Exception:
        pass


@router.callback_query(lambda c: c.data and c.data.startswith("admin:own_sponsor_del_confirm:"))
async def cb_own_sponsor_del_confirm(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    sponsor_id = int(callback.data.split(":")[2])
    deleted = await OwnSponsorRepository(session).delete(sponsor_id)
    await callback.answer("✅ Удалено" if deleted else "❌ Не найдено", show_alert=True)
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.callback_query(lambda c: c.data and c.data.startswith("admin:own_sponsor_del:"))
async def cb_own_sponsor_del(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    sponsor_id = int(callback.data.split(":")[2])
    sponsor = await OwnSponsorRepository(session).get(sponsor_id)
    if not sponsor:
        await callback.answer("❌ Не найдено.", show_alert=True)
        return
    await callback.message.answer(
        f"🗑 Удалить спонсора <b>{sponsor.name}</b>? Статистика набора будет потеряна.",
        parse_mode="HTML",
        reply_markup=own_sponsor_delete_confirm_kb(sponsor_id),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:own_sponsor_new")
async def cb_own_sponsor_new(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.set_state(AdminOwnSponsorStates.enter_kind)
    await callback.message.answer(
        "📢 <b>Новый спонсор</b>\n\nВыбери тип:",
        parse_mode="HTML",
        reply_markup=own_sponsor_kind_kb(),
    )
    await callback.answer()


@router.callback_query(
    AdminOwnSponsorStates.enter_kind,
    lambda c: c.data and c.data.startswith("admin:own_sponsor_kind:"),
)
async def cb_own_sponsor_kind(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    kind = callback.data.split(":")[2]
    if kind not in ("channel", "bot"):
        await callback.answer()
        return
    await state.update_data(kind=kind)
    if kind == "channel":
        await state.set_state(AdminOwnSponsorStates.enter_target)
        await callback.message.answer(
            "🎯 Пришли @username или numeric ID канала/чата (бот уже должен быть там админом — иначе проверка подписок не сработает):",
            reply_markup=own_sponsor_cancel_kb(),
        )
    else:
        await state.set_state(AdminOwnSponsorStates.enter_url)
        await callback.message.answer(
            "🔗 Пришли ссылку на бота (например https://t.me/SomeBot?start=promo):",
            reply_markup=own_sponsor_cancel_kb(),
        )
    await callback.answer()


@router.message(AdminOwnSponsorStates.enter_target)
async def msg_own_sponsor_target(message: Message, state: FSMContext, db_user: User, bot: Bot) -> None:
    if not _is_admin(db_user): return
    target = (message.text or "").strip()
    if not target:
        await message.answer("❌ Пришли @username или ID канала/чата:", reply_markup=own_sponsor_cancel_kb())
        return
    if not await is_bot_admin_in_chat(bot, target):
        await message.answer(
            "❌ Бот не является админом в этом канале/чате (или он не найден). "
            "Добавь бота туда админом и пришли @username/ID ещё раз:",
            reply_markup=own_sponsor_cancel_kb(),
        )
        return
    await state.update_data(target=target)
    await state.set_state(AdminOwnSponsorStates.enter_url)
    await message.answer(
        "🔗 Пришли ссылку, которую увидит пользователь для подписки (если канал приватный — инвайт-ссылку):",
        reply_markup=own_sponsor_cancel_kb(),
    )


@router.message(AdminOwnSponsorStates.enter_url)
async def msg_own_sponsor_url(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    url = (message.text or "").strip()
    if not url:
        await message.answer("❌ Пришли ссылку:", reply_markup=own_sponsor_cancel_kb())
        return
    await state.update_data(url=url)
    await state.set_state(AdminOwnSponsorStates.enter_name)
    await message.answer("✏️ Введи название спонсора (для админ-списка):", reply_markup=own_sponsor_cancel_kb())


@router.message(AdminOwnSponsorStates.enter_name)
async def msg_own_sponsor_name(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Введи название:", reply_markup=own_sponsor_cancel_kb())
        return
    await state.update_data(name=name)
    await state.set_state(AdminOwnSponsorStates.enter_target_count)
    data = await state.get_data()
    unit = "подписчиков" if data.get("kind") == "channel" else "переходов"
    await message.answer(f"🎯 Сколько {unit} набрать перед автоотключением?", reply_markup=own_sponsor_cancel_kb())


@router.message(AdminOwnSponsorStates.enter_target_count)
async def msg_own_sponsor_target_count(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    try:
        target_count = int((message.text or "").strip())
        if target_count <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи целое положительное число:", reply_markup=own_sponsor_cancel_kb())
        return

    data = await state.get_data()
    await state.clear()
    repo = OwnSponsorRepository(session)
    sponsor = await repo.create(
        kind=data["kind"],
        url=data["url"],
        name=data["name"],
        target_count=target_count,
        target=data.get("target"),
    )
    await message.answer(
        f"✅ <b>Спонсор добавлен!</b>\n\n{_KIND_LABELS.get(sponsor.kind, sponsor.kind)} {sponsor.name}\n"
        f"Цель: <b>{sponsor.target_count}</b>",
        parse_mode="HTML",
        reply_markup=back_to_admin_kb(),
    )
