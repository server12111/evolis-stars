from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Числовые настройки — по одной в ряд
NUMERIC_SETTINGS = [
    ("referral_reward", "🎁 Награда за реферала"),
    ("bonus_min", "💰 Мин. бонус"),
    ("bonus_max", "💰 Макс. бонус"),
    ("tasks_reward", "📋 Награда за задание"),
    ("min_tasks_for_referral", "📊 Мин. заданий для реферала"),
    ("withdraw_min", "⭐ Мин. сумма вывода"),
    ("duel_commission", "⚔️ Комиссия дуэлей"),
    ("duel_min_refs", "⚔️ Мин. рефералов для дуэлей"),
    ("lottery_min_refs", "🎟 Мин. рефералов для лотереи"),
    ("sponsor_max_channels", "📢 Макс. каналов спонсоров"),
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
