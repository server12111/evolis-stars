from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Числовые настройки — по одной в ряд
NUMERIC_SETTINGS = [
    ("referral_reward", "🎁 Награда за реферала"),
    ("referral_reward_premium", "💎 Награда за Premium-реферала"),
    ("min_sponsors_for_reward", "📢 Мин. спонсоров для награды"),
    ("referral_bonus_10", "🏅 Бонус за 10 рефералов"),
    ("referral_bonus_15", "🏅 Бонус за 15 рефералов"),
    ("referral_bonus_20", "🏅 Бонус за 20 рефералов"),
    ("referral_bonus_25", "🏅 Бонус за 25 рефералов"),
    ("referral_bonus_30", "🏅 Бонус за 30 рефералов"),
    ("referral_bonus_35", "🏅 Бонус за 35 рефералов"),
    ("referral_bonus_40", "🏅 Бонус за 40 рефералов"),
    ("referral_bonus_45", "🏅 Бонус за 45 рефералов"),
    ("referral_bonus_50", "🏅 Бонус за 50 рефералов (VIP)"),
    ("referral_bonus_55", "🏅 Бонус за 55 рефералов"),
    ("referral_bonus_60", "🏅 Бонус за 60 рефералов"),
    ("referral_bonus_67", "🏅 Бонус за 67 рефералов"),
    ("referral_bonus_70", "🏅 Бонус за 70 рефералов"),
    ("referral_bonus_76", "🏅 Бонус за 76 рефералов"),
    ("referral_bonus_80", "🏅 Бонус за 80 рефералов"),
    ("referral_bonus_90", "🏅 Бонус за 90 рефералов"),
    ("referral_bonus_100", "🏅 Бонус за 100+ рефералов (навсегда)"),
    ("bonus_min", "💰 Мин. бонус"),
    ("bonus_max", "💰 Макс. бонус"),
    ("tasks_reward", "📋 Награда за задание"),
    ("withdraw_min", "⭐ Мин. сумма вывода"),
    ("duel_commission", "⚔️ Комиссия дуэлей"),
    ("duel_min_refs", "⚔️ Мин. рефералов для дуэлей"),
    ("lottery_min_refs", "🎟 Мин. рефералов для лотереи"),
    ("games_min_refs", "🎮 Мин. рефералов для игр"),
    ("sponsor_max_channels", "📢 Макс. каналов спонсоров"),
    ("mines_min_bet", "💣 Мин. ставка в Минах"),
    ("mines_house_edge", "💣 Комиссия казино в Минах"),
    ("mines_max_coeff", "💣 Макс. множитель в Минах"),
]

# Переключатели — по два в ряд
TOGGLE_PAIRS = [
    ("bonus_enabled", "🎁 Бонус"),
    ("withdraw_enabled", "💸 Вывод"),
    ("games_enabled", "🎮 Игры"),
    ("tasks_enabled", "📋 Задания"),
]


def settings_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, label in NUMERIC_SETTINGS:
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
