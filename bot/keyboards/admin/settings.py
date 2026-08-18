from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Числовые настройки — по одной в ряд
NUMERIC_SETTINGS = [
    ("referral_reward_0_3", "🎁 Награда (0-3 спонсора)"),
    ("referral_reward_4_5", "🎁 Награда (4-5 спонсоров)"),
    ("referral_reward_6_7", "🎁 Награда (6-7 спонсоров)"),
    ("referral_reward_8_9", "🎁 Награда (8-9 спонсоров)"),
    ("referral_reward_10_12", "🎁 Награда (10-12 спонсоров)"),
    ("referral_reward_13_16", "🎁 Награда (13-16 спонсоров)"),
    ("referral_reward_premium", "💎 Premium-награда (фикс.)"),
    ("min_sponsors_for_reward", "📢 Мин. спонсоров для награды"),
    ("referral_bonus_10", "🏅 Бонус за 10 рефералов"),
    ("referral_bonus_20", "🏅 Бонус за 20 рефералов"),
    ("referral_bonus_30", "🏅 Бонус за 30 рефералов"),
    ("referral_bonus_40", "🏅 Бонус за 40 рефералов"),
    ("referral_bonus_50", "🏅 Бонус за 50 рефералов (VIP)"),
    ("referral_bonus_65", "🏅 Бонус за 65 рефералов"),
    ("referral_bonus_80", "🏅 Бонус за 80 рефералов"),
    ("referral_bonus_90", "🏅 Бонус за 90 рефералов"),
    ("referral_bonus_100", "🏅 Бонус за 100 рефералов"),
    ("referral_bonus_120", "🏅 Бонус за 120 рефералов"),
    ("referral_bonus_140", "🏅 Бонус за 140 рефералов"),
    ("referral_bonus_145", "🏅 Бонус за 145 рефералов"),
    ("referral_bonus_150", "🏅 Бонус за 150 рефералов"),
    ("referral_bonus_155", "🏅 Бонус за 155 рефералов"),
    ("referral_bonus_170", "🏅 Бонус за 170 рефералов"),
    ("referral_bonus_190", "🏅 Бонус за 190 рефералов"),
    ("referral_bonus_200", "🏅 Бонус за 200 рефералов (Premium)"),
    ("referral_bonus_250", "🏅 Бонус за 250 рефералов"),
    ("referral_bonus_350", "🏅 Бонус за 350 рефералов"),
    ("referral_bonus_450", "🏅 Бонус за 450 рефералов"),
    ("referral_recurring_200", "♻️ Ставка с 200-го (Premium)"),
    ("referral_recurring_300", "♻️ Ставка с 300-го (Sigma)"),
    ("referral_recurring_400", "♻️ Ставка с 400-го"),
    ("referral_recurring_500", "♻️ Ставка с 500-го (Good)"),
    ("bonus_min", "💰 Мин. бонус"),
    ("bonus_max", "💰 Макс. бонус"),
    ("tasks_reward", "📋 Награда за задание"),
    ("duel_commission", "⚔️ Комиссия дуэлей"),
    ("duel_min_refs", "⚔️ Мин. рефералов для дуэлей"),
    ("lottery_min_refs", "🎟 Мин. рефералов для лотереи"),
    ("games_min_refs", "🎮 Мин. рефералов для игр"),
    ("sponsor_max_channels", "📢 Макс. каналов спонсоров"),
    ("mines_min_bet", "💣 Мин. ставка в Минах"),
    ("mines_house_edge", "💣 Комиссия казино в Минах"),
    ("mines_max_coeff", "💣 Макс. множитель в Минах"),
    ("rp_exchange_rate", "🔄 Курс покупки RP⭐️"),
    ("door_coeff_1", "🚪 Двери — множитель 1 уровня"),
    ("door_coeff_2", "🚪 Двери — множитель 2 уровня"),
    ("door_coeff_3", "🚪 Двери — множитель 3 уровня"),
    ("door_coeff_4", "🚪 Двери — множитель 4 уровня"),
    ("door_coeff_5", "🚪 Двери — множитель 5 уровня"),
    ("door_coeff_6", "🚪 Двери — множитель 6 уровня"),
    ("door_coeff_7", "🚪 Двери — множитель 7 уровня"),
    ("door_coeff_8", "🚪 Двери — множитель 8 уровня"),
    ("door_coeff_9", "🚪 Двери — множитель 9 уровня"),
    ("door_coeff_10", "🚪 Двери — множитель 10 уровня"),
    ("vc_min_withdrawal", "💎 Мин. вывод GRAM"),
    ("vc_max_withdrawal", "💎 Макс. вывод GRAM"),
    ("vc_rate_min", "💎 Мин. курс GRAM (при мин. сумме)"),
    ("vc_rate_max", "💎 Макс. курс GRAM (при макс. сумме)"),
    ("crypto_min_withdrawal", "🪙 Мин. вывод крипты"),
    ("crypto_rp_usd_rate", "🪙 Курс крипты ($ за 1 RP⭐️)"),
]

# Текстовые настройки (не числа) — по одной в ряд, тот же generic-флоу
# редактирования (admin:settings_edit:{key}), просто без float-парсинга.
TEXT_SETTINGS = [
    ("vc_mandatory_channel", "💬 Чат для вывода GRAM"),
]

# Переключатели — по два в ряд
TOGGLE_PAIRS = [
    ("bonus_enabled", "🎁 Бонус"),
    ("withdraw_enabled", "💸 Вывод"),
    ("games_enabled", "🎮 Игры"),
    ("tasks_enabled", "📋 Задания"),
    ("withdraw_vc_enabled", "💎 Вывод GRAM"),
    ("withdraw_crypto_enabled", "🪙 Вывод крипты"),
]


def settings_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, label in NUMERIC_SETTINGS:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"admin:settings_edit:{key}"))
    for key, label in TEXT_SETTINGS:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"admin:settings_edit:{key}"))

    # Toggles по 2 в ряд
    toggle_buttons = [
        InlineKeyboardButton(text=label, callback_data=f"admin:settings_toggle:{key}")
        for key, label in TOGGLE_PAIRS
    ]
    for i in range(0, len(toggle_buttons), 2):
        builder.row(*toggle_buttons[i:i+2])

    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def settings_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:settings"),
    ]])
