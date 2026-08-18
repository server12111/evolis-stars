import math
from html import escape

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
    "referral_reward": ("🎁 Награда за реферала (3-5 спонсоров)", "число RP⭐️"),
    "referral_reward_above_5": ("🎁 Награда за реферала (6-8 спонсоров)", "число RP⭐️"),
    "referral_reward_top": ("🎁 Награда за реферала (9+ спонсоров)", "число RP⭐️"),
    "referral_reward_premium": ("💎 Награда за Premium-реферала (фикс.)", "число RP⭐️"),
    "min_sponsors_for_reward": ("📢 Мин. спонсоров для награды", "целое число"),
    "referral_bonus_10": ("🏅 Бонус за 10 рефералов", "число RP⭐️"),
    "referral_bonus_20": ("🏅 Бонус за 20 рефералов", "число RP⭐️"),
    "referral_bonus_30": ("🏅 Бонус за 30 рефералов", "число RP⭐️"),
    "referral_bonus_40": ("🏅 Бонус за 40 рефералов", "число RP⭐️"),
    "referral_bonus_50": ("🏅 Бонус за 50 рефералов (VIP)", "число RP⭐️"),
    "referral_bonus_65": ("🏅 Бонус за 65 рефералов", "число RP⭐️"),
    "referral_bonus_80": ("🏅 Бонус за 80 рефералов", "число RP⭐️"),
    "referral_bonus_90": ("🏅 Бонус за 90 рефералов", "число RP⭐️"),
    "referral_bonus_100": ("🏅 Бонус за 100 рефералов", "число RP⭐️"),
    "referral_bonus_120": ("🏅 Бонус за 120 рефералов", "число RP⭐️"),
    "referral_bonus_140": ("🏅 Бонус за 140 рефералов", "число RP⭐️"),
    "referral_bonus_145": ("🏅 Бонус за 145 рефералов", "число RP⭐️"),
    "referral_bonus_150": ("🏅 Бонус за 150 рефералов", "число RP⭐️"),
    "referral_bonus_155": ("🏅 Бонус за 155 рефералов", "число RP⭐️"),
    "referral_bonus_170": ("🏅 Бонус за 170 рефералов", "число RP⭐️"),
    "referral_bonus_190": ("🏅 Бонус за 190 рефералов", "число RP⭐️"),
    "referral_bonus_200": ("🏅 Бонус за 200 рефералов (Premium)", "число RP⭐️"),
    "referral_bonus_250": ("🏅 Бонус за 250 рефералов", "число RP⭐️"),
    "referral_bonus_350": ("🏅 Бонус за 350 рефералов", "число RP⭐️"),
    "referral_bonus_450": ("🏅 Бонус за 450 рефералов", "число RP⭐️"),
    "referral_recurring_200": ("♻️ Ставка за каждого с 200-го (Premium)", "число RP⭐️"),
    "referral_recurring_300": ("♻️ Ставка за каждого с 300-го (Sigma)", "число RP⭐️"),
    "referral_recurring_400": ("♻️ Ставка за каждого с 400-го", "число RP⭐️"),
    "referral_recurring_500": ("♻️ Ставка за каждого с 500-го (Good)", "число RP⭐️"),
    "bonus_min": ("🎁 Мин. ежедн. бонус", "число RP⭐️"),
    "bonus_max": ("🎁 Макс. ежедн. бонус", "число RP⭐️"),
    "tasks_reward": ("📋 Награда за задание", "число RP⭐️"),
    "duel_commission": ("⚔️ Комиссия дуэлей %", "число 0-100"),
    "duel_min_refs": ("⚔️ Мин. рефералов для дуэлей", "целое число"),
    "lottery_min_refs": ("🎟 Мин. рефералов для лотереи", "целое число"),
    "games_min_refs": ("🎮 Мин. рефералов для игр", "целое число"),
    "sponsor_max_channels": ("📢 Макс. каналов спонсоров", "целое число"),
    "mines_min_bet": ("💣 Мин. ставка в Минах", "число RP⭐️"),
    "mines_house_edge": ("💣 Комиссия казино в Минах", "число 0-1 (напр. 0.1 = 10%)"),
    "mines_max_coeff": ("💣 Макс. множитель в Минах", "число (напр. 10)"),
    "rp_exchange_rate": ("🔄 Курс покупки RP⭐️ (RP⭐️ за 1 Telegram ⭐)", "число > 0, напр. 1"),
    "door_coeff_1": ("🚪 Двери — множитель 1 уровня", "число (напр. 1.2)"),
    "door_coeff_2": ("🚪 Двери — множитель 2 уровня", "число (напр. 2.4)"),
    "door_coeff_3": ("🚪 Двери — множитель 3 уровня", "число (напр. 4.8)"),
    "door_coeff_4": ("🚪 Двери — множитель 4 уровня", "число (напр. 9.6)"),
    "door_coeff_5": ("🚪 Двери — множитель 5 уровня", "число"),
    "door_coeff_6": ("🚪 Двери — множитель 6 уровня", "число"),
    "door_coeff_7": ("🚪 Двери — множитель 7 уровня", "число"),
    "door_coeff_8": ("🚪 Двери — множитель 8 уровня", "число"),
    "door_coeff_9": ("🚪 Двери — множитель 9 уровня", "число"),
    "door_coeff_10": ("🚪 Двери — множитель 10 уровня", "число"),
    "vc_min_withdrawal": ("💎 Мин. вывод GRAM", "целое число, напр. 10000"),
    "vc_max_withdrawal": ("💎 Макс. вывод GRAM", "целое число, напр. 500000"),
    "vc_rate_min": ("💎 Мин. курс GRAM (при мин. сумме) — GRAM за 1 RP⭐️", "число, напр. 400"),
    "vc_rate_max": ("💎 Макс. курс GRAM (при макс. сумме) — GRAM за 1 RP⭐️", "число, напр. 800"),
    "crypto_min_withdrawal": ("🪙 Мин. вывод крипты", "целое число RP⭐️, напр. 50"),
    "crypto_rp_usd_rate": ("🪙 Курс крипты ($ за 1 RP⭐️)", "число, напр. 0.005"),
}

# Plain-text (non-numeric) admin settings -- msg_setting_value branches to a
# simple non-empty-string check for these instead of the float parsing used
# for everything in SETTING_LABELS.
TEXT_SETTING_LABELS = {
    "vc_mandatory_channel": ("💬 Обязательный чат для вывода GRAM", "ссылка, например https://t.me/VirusikChat"),
}

TOGGLE_SETTINGS = {
    "bonus_enabled": "🎁 Бонус",
    "withdraw_enabled": "🌟 Вывод",
    "games_enabled": "🎮 Игры",
    "tasks_enabled": "📋 Задания",
    "withdraw_vc_enabled": "💎 Вывод GRAM",
    "withdraw_crypto_enabled": "🪙 Вывод крипты",
}


@router.callback_query(lambda c: c.data == "admin:settings")
async def cb_settings(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    repo = SettingsRepository(session)
    referral_reward = await repo.get_float("referral_reward", 3.0)
    referral_reward_above_5 = await repo.get_float("referral_reward_above_5", 3.5)
    referral_reward_premium = await repo.get_float("referral_reward_premium", 4.5)
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
        f"🎁 Награда за реферала: 3-5сп <b>{referral_reward:.2f}</b> / 6+сп <b>{referral_reward_above_5:.2f}</b> RP⭐️\n"
        f"💎 Награда за Premium-реферала: <b>{referral_reward_premium:.2f} RP⭐️</b> (фикс.)\n"
        f"📢 Мин. спонсоров для награды: <b>{min_sponsors}</b>\n"
        f"🎁 Бонус: <b>{bonus_min:.1f}–{bonus_max:.1f} RP⭐️</b>\n"
        f"📋 Награда за задание: <b>{task_reward:.1f} RP⭐️</b>\n\n"
        f"🎁 Бонус: {'✅' if bonus_on else '❌'} | "
        f"🌟 Вывод: {'✅' if withdraw_on else '❌'}\n"
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
    if key not in SETTING_LABELS and key not in TEXT_SETTING_LABELS:
        await callback.answer("❓ Неизвестная настройка.", show_alert=True)
        return
    label, hint = (SETTING_LABELS.get(key) or TEXT_SETTING_LABELS.get(key))
    await state.set_state(AdminSettingsStates.enter_value)
    await state.update_data(setting_key=key)
    await callback.message.answer(f"✏️ <b>{label}</b>\n\nВведи новое значение ({hint}):", parse_mode="HTML", reply_markup=settings_cancel_kb())
    await callback.answer()


@router.message(AdminSettingsStates.enter_value)
async def msg_setting_value(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    data = await state.get_data()
    key = data["setting_key"]

    if key in TEXT_SETTING_LABELS:
        raw = (message.text or "").strip()
        if not raw:
            await message.answer("❌ Введи непустое значение:", reply_markup=settings_cancel_kb())
            return
        if key == "vc_mandatory_channel" and not raw.startswith(("http://", "https://", "tg://")):
            # Used as an InlineKeyboardButton(url=...) -- Telegram rejects
            # non-http(s)/tg URLs at send time, which would otherwise crash
            # every subscribe-gate prompt until an admin noticed.
            await message.answer(
                "❌ Нужна полная ссылка (https://t.me/... или tg://...), а не @username:",
                reply_markup=settings_cancel_kb(),
            )
            return
        await state.clear()
        await SettingsRepository(session).set(key, raw)
        label, _ = TEXT_SETTING_LABELS[key]
        await message.answer(f"✅ <b>{label}</b> = <b>{escape(raw)}</b>", parse_mode="HTML", reply_markup=back_to_admin_kb())
        return

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
        "vc_min_withdrawal",
        "vc_max_withdrawal",
        "crypto_min_withdrawal",
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
    if key == "mines_house_edge" and val >= 1:
        # val == 1 would zero out every non-tabled payout (mines.py computes
        # raw = (1/prob) * (1 - house_edge)) — a 100% house edge that
        # silently breaks the game rather than a valid extreme setting.
        await message.answer(
            "❌ Комиссия казино в Минах — доля от 0 до 1, строго меньше 1 (напр. 0.1 = 10%):",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "mines_max_coeff" and val <= 0:
        # Same failure mode as above: mines_coeff() clamps every payout to
        # min(raw, max_coeff), so max_coeff == 0 zeroes everything out.
        await message.answer(
            "❌ Макс. множитель должен быть больше 0:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "rp_exchange_rate" and val <= 0:
        await message.answer(
            "❌ Курс обмена должен быть больше 0:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "bonus_min":
        current_max = await repo.get_float("bonus_max", 1.0)
        if val > current_max:
            await message.answer(
                f"❌ Мин. бонус не может быть больше макс. ({current_max}):",
                reply_markup=settings_cancel_kb(),
            )
            return
    if key == "bonus_max":
        current_min = await repo.get_float("bonus_min", 0.1)
        if val < current_min:
            await message.answer(
                f"❌ Макс. бонус не может быть меньше мин. ({current_min}):",
                reply_markup=settings_cancel_kb(),
            )
            return
    if key in ("vc_rate_min", "vc_rate_max") and val <= 0:
        await message.answer(
            "❌ Курс GRAM должен быть больше 0:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "vc_rate_min":
        current_max = await repo.get_float("vc_rate_max", 800.0)
        if val > current_max:
            await message.answer(
                f"❌ Мин. курс GRAM не может быть больше макс. ({current_max:g}):",
                reply_markup=settings_cancel_kb(),
            )
            return
    if key == "vc_rate_max":
        current_min = await repo.get_float("vc_rate_min", 400.0)
        if val < current_min:
            await message.answer(
                f"❌ Макс. курс GRAM не может быть меньше мин. ({current_min:g}):",
                reply_markup=settings_cancel_kb(),
            )
            return
    if key == "vc_min_withdrawal":
        current_max = await repo.get_int("vc_max_withdrawal", 500000)
        if val > current_max:
            await message.answer(
                f"❌ Мин. вывод GRAM не может быть больше макс. ({current_max}):",
                reply_markup=settings_cancel_kb(),
            )
            return
    if key == "vc_max_withdrawal":
        current_min = await repo.get_int("vc_min_withdrawal", 10000)
        if val < current_min:
            await message.answer(
                f"❌ Макс. вывод GRAM не может быть меньше мин. ({current_min}):",
                reply_markup=settings_cancel_kb(),
            )
            return
    if key == "crypto_min_withdrawal" and val <= 0:
        await message.answer(
            "❌ Мин. вывод крипты должен быть больше 0:",
            reply_markup=settings_cancel_kb(),
        )
        return
    if key == "crypto_rp_usd_rate" and val <= 0:
        await message.answer(
            "❌ Курс крипты должен быть больше 0:",
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
