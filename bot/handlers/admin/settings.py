import math

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.main import back_to_admin_kb
from bot.keyboards.admin.settings import settings_cancel_kb
from bot.keyboards.admin.settings import settings_kb as settings_menu_kb
from bot.states.admin import AdminSettingsStates

router = Router()

SETTING_LABELS = {
    "referral_reward": ("🎁 Награда за реферала", "число ⭐"),
    "min_sponsors_for_reward": ("📢 Мин. спонсоров для награды", "целое число"),
    "referral_bonus_10": ("🏅 Бонус за 10 рефералов", "число ⭐"),
    "referral_bonus_25": ("🏅 Бонус за 25 рефералов", "число ⭐"),
    "referral_bonus_30": ("🏅 Бонус за 30 рефералов", "число ⭐"),
    "referral_bonus_50": ("🏅 Бонус за 50 рефералов (VIP)", "число ⭐"),
    "referral_bonus_55": ("🏅 Бонус за 55 рефералов", "число ⭐"),
    "referral_bonus_60": ("🏅 Бонус за 60 рефералов", "число ⭐"),
    "referral_bonus_70": ("🏅 Бонус за 70+ рефералов", "число ⭐"),
    "bonus_min": ("🎁 Мин. ежедн. бонус", "число ⭐"),
    "bonus_max": ("🎁 Макс. ежедн. бонус", "число ⭐"),
    "tasks_reward": ("📋 Награда за задание", "число ⭐"),
    "withdraw_min": ("⭐ Мин. сумма вывода", "число ⭐"),
    "duel_commission": ("⚔️ Комиссия дуэлей %", "число 0-100"),
    "duel_min_refs": ("⚔️ Мин. рефералов для дуэлей", "целое число"),
    "lottery_min_refs": ("🎟 Мин. рефералов для лотереи", "целое число"),
    "games_min_refs": ("🎮 Мин. рефералов для игр", "целое число"),
    "sponsor_max_channels": ("📢 Макс. каналов спонсоров", "целое число"),
    "mines_min_bet": ("💣 Мин. ставка в Минах", "число ⭐"),
    "mines_house_edge": ("💣 Комиссия казино в Минах", "число 0-1 (напр. 0.1 = 10%)"),
    "mines_max_coeff": ("💣 Макс. множитель в Минах", "число (напр. 10)"),
}

TOGGLE_SETTINGS = {
    "bonus_enabled": "🎁 Бонус",
    "withdraw_enabled": "⭐ Вывод",
    "games_enabled": "🎮 Игры",
    "tasks_enabled": "📋 Задания",
}


@router.callback_query(lambda c: c.data == "admin:settings")
async def cb_settings(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    repo = SettingsRepository(session)
    referral_reward = await repo.get_float("referral_reward", 4.0)
    min_sponsors = await repo.get_int("min_sponsors_for_reward", 3)
    bonus_min = await repo.get_float("bonus_min", 0.1)
    bonus_max = await repo.get_float("bonus_max", 1.0)
    task_reward = await repo.get_float("tasks_reward", 0.3)
    bonus_on = await repo.get_bool("bonus_enabled", True)
    withdraw_on = await repo.get_bool("withdraw_enabled", True)
    games_on = await repo.get_bool("games_enabled", True)
    tasks_on = await repo.get_bool("tasks_enabled", True)

    text = (
        f"⚙️ <b>Глобальные настройки</b>\n\n"
        f"🎁 Награда за реферала: <b>{referral_reward:.2f} ⭐</b>\n"
        f"📢 Мин. спонсоров для награды: <b>{min_sponsors}</b>\n"
        f"🎁 Бонус: <b>{bonus_min:.1f}–{bonus_max:.1f} ⭐</b>\n"
        f"📋 Награда за задание: <b>{task_reward:.1f} ⭐</b>\n\n"
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
        "duel_min_refs",
        "lottery_min_refs",
        "games_min_refs",
        "sponsor_max_channels",
        "min_sponsors_for_reward",
    }
    if key in integer_keys and not val.is_integer():
        await message.answer(
            "❌ Для этой настройки нужно целое число:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "sponsor_max_channels" and val > 20:
        await message.answer(
            "❌ За один раз можно показать не больше 20 спонсоров:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "duel_commission" and val > 100:
        await message.answer(
            "❌ Комиссия должна быть от 0 до 100:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "mines_house_edge" and val > 1:
        await message.answer(
            "❌ Комиссия казино в Минах — доля от 0 до 1 (напр. 0.1 = 10%):",
            reply_markup=settings_cancel_kb(),
        )
        return
    await state.clear()
    stored_value = str(int(val)) if key in integer_keys else str(val)
    await repo.set(key, stored_value)
    label, _ = SETTING_LABELS[key]
    await message.answer(f"✅ <b>{label}</b> = <b>{val}</b>", parse_mode="HTML", reply_markup=back_to_admin_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("admin:settings_toggle:"))
async def cb_set_toggle(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
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
    await cb_settings(callback, db_user, session, state)
