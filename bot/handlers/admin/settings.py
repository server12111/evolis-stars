import math

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.main import back_to_admin_kb
from bot.keyboards.admin.settings import phone_codes_kb, settings_cancel_kb
from bot.keyboards.admin.settings import settings_kb as settings_menu_kb
from bot.services.phone import (
    COUNTRY_CODE_NAMES,
    allowed_country_codes,
    parse_country_codes,
)
from bot.states.admin import AdminPhoneCodesStates, AdminSettingsStates

router = Router()

SETTING_LABELS = {
    "referral_reward": ("💰 Реферальная награда", "число ⭐"),
    "bonus_min": ("🎁 Мин. ежедн. бонус", "число ⭐"),
    "bonus_max": ("🎁 Макс. ежедн. бонус", "число ⭐"),
    "min_tasks_for_referral": ("📋 Мин. заданий для реф.", "целое число"),
    "tasks_reward": ("📋 Награда за задание", "число ⭐"),
    "withdraw_min": ("⭐ Мин. сумма вывода", "число ⭐"),
    "duel_commission": ("⚔️ Комиссия дуэлей %", "число 0-100"),
    "duel_min_refs": ("⚔️ Мин. рефералов для дуэлей", "целое число"),
    "lottery_min_refs": ("🎟 Мин. рефералов для лотереи", "целое число"),
    "sponsor_max_channels": ("📢 Макс. каналов спонсоров", "целое число"),
}

TOGGLE_SETTINGS = {
    "bonus_enabled": "🎁 Бонус",
    "withdraw_enabled": "⭐ Вывод",
    "games_enabled": "🎮 Игры",
    "tasks_enabled": "📋 Задания",
}


@router.callback_query(lambda c: c.data == "admin:settings")
async def cb_settings(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    repo = SettingsRepository(session)
    ref_reward = await repo.get_float("referral_reward", 3.0)
    bonus_min = await repo.get_float("bonus_min", 0.1)
    bonus_max = await repo.get_float("bonus_max", 1.0)
    min_tasks = await repo.get_int("min_tasks_for_referral", 5)
    task_reward = await repo.get_float("tasks_reward", 0.3)
    bonus_on = await repo.get_bool("bonus_enabled", True)
    withdraw_on = await repo.get_bool("withdraw_enabled", True)
    games_on = await repo.get_bool("games_enabled", True)
    tasks_on = await repo.get_bool("tasks_enabled", True)
    codes = allowed_country_codes(await repo.get("phone_allowed_codes"))
    codes_text = ", ".join(f"+{code}" for code in codes)

    text = (
        f"⚙️ <b>Глобальные настройки</b>\n\n"
        f"💰 Реф. награда: <b>{ref_reward:.1f} ⭐</b>\n"
        f"🎁 Бонус: <b>{bonus_min:.1f}–{bonus_max:.1f} ⭐</b>\n"
        f"📋 Мин. заданий для реф.: <b>{min_tasks}</b>\n"
        f"📋 Награда за задание: <b>{task_reward:.1f} ⭐</b>\n\n"
        f"🌍 Коды номеров: <b>{codes_text}</b>\n\n"
        f"🎁 Бонус: {'✅' if bonus_on else '❌'} | "
        f"⭐ Вывод: {'✅' if withdraw_on else '❌'}\n"
        f"🎮 Игры: {'✅' if games_on else '❌'} | "
        f"📋 Задания: {'✅' if tasks_on else '❌'}"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=settings_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=settings_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:phone_codes")
async def cb_phone_codes(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    repo = SettingsRepository(session)
    codes = allowed_country_codes(await repo.get("phone_allowed_codes"))
    lines = [
        f"+{code} — {COUNTRY_CODE_NAMES.get(code, 'добавленный код')}"
        for code in codes
    ]
    text = (
        "🌍 <b>Разрешённые коды номеров</b>\n\n"
        + "\n".join(lines)
        + "\n\nМожно добавить один или несколько кодов через запятую, например: <code>995, +48</code>."
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=phone_codes_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=phone_codes_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:phone_codes_add")
async def cb_phone_codes_add(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminPhoneCodesStates.enter_code)
    await callback.message.answer(
        "➕ Введите код страны (например, <code>+995</code>) или несколько кодов через запятую:",
        parse_mode="HTML",
        reply_markup=settings_cancel_kb(),
    )
    await callback.answer()


@router.message(AdminPhoneCodesStates.enter_code)
async def msg_phone_code(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user):
        return
    added = parse_country_codes(message.text)
    if not added:
        await message.answer("❌ Укажите цифровой код, например <code>+995</code>.", parse_mode="HTML")
        return

    repo = SettingsRepository(session)
    current = set(allowed_country_codes(await repo.get("phone_allowed_codes")))
    current.update(added)
    value = ",".join(sorted(current, key=lambda item: (len(item), item)))
    await repo.set("phone_allowed_codes", value)
    await state.clear()
    await message.answer(
        "✅ Коды добавлены: " + ", ".join(f"+{code}" for code in added),
        reply_markup=back_to_admin_kb(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("admin:settings_edit:"))
async def cb_set_edit(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    key = callback.data.split(":", 2)[2]
    if key not in SETTING_LABELS:
        await callback.answer("❓ Неизвестная настройка.", show_alert=True)
        return
    label, hint = SETTING_LABELS[key]
    await state.set_state(AdminSettingsStates.enter_value)
    await state.update_data(setting_key=key)
    await callback.message.answer(f"✏️ <b>{label}</b>\n\nВведи новое значение ({hint}):", parse_mode="HTML", reply_markup=settings_cancel_kb())
    await callback.answer()


@router.message(AdminSettingsStates.enter_value)
async def msg_setting_value(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    data = await state.get_data()
    key = data["setting_key"]
    text = (message.text or "").strip().replace(",", ".")
    try:
        val = float(text)
        if not math.isfinite(val) or val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи корректное число:", reply_markup=settings_cancel_kb())
        return
    repo = SettingsRepository(session)
    integer_keys = {
        "min_tasks_for_referral",
        "duel_min_refs",
        "lottery_min_refs",
        "sponsor_max_channels",
    }
    if key in integer_keys and not val.is_integer():
        await message.answer(
            "❌ Для этой настройки нужно целое число:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "sponsor_max_channels" and val > 6:
        await message.answer(
            "❌ За один раз можно показать не больше 6 спонсоров:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "duel_commission" and val > 100:
        await message.answer(
            "❌ Комиссия должна быть от 0 до 100:",
            reply_markup=settings_cancel_kb(),
        )
        return
    await state.clear()
    stored_value = str(int(val)) if key in integer_keys else str(val)
    await repo.set(key, stored_value)
    label, _ = SETTING_LABELS[key]
    await message.answer(f"✅ <b>{label}</b> = <b>{val}</b>", parse_mode="HTML", reply_markup=back_to_admin_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("admin:settings_toggle:"))
async def cb_set_toggle(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    key = callback.data.split(":", 2)[2]
    if key not in TOGGLE_SETTINGS:
        await callback.answer("❓ Неизвестная настройка.", show_alert=True)
        return
    repo = SettingsRepository(session)
    current = await repo.get_bool(key, True)
    await repo.set(key, "0" if current else "1")
    label = TOGGLE_SETTINGS[key]
    status = "включён" if not current else "отключён"
    await callback.answer(f"{label} {status}")
    await cb_settings(callback, db_user, session)
