from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_main_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
        InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast"),
    )
    builder.row(
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users"),
        InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin:promo"),
    )
    builder.row(
        InlineKeyboardButton(text="📋 Задания", callback_data="admin:tasks"),
        InlineKeyboardButton(text="🎮 Игры", callback_data="admin:games"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin:settings"),
        InlineKeyboardButton(text="🖼 Медиа и тексты", callback_data="admin:media"),
    )
    return builder.as_markup()


def back_to_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"),
    ]])
