from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Заработать", callback_data="menu:earn", style="success"),
    )
    builder.row(
        InlineKeyboardButton(text="🎁 Бонус", callback_data="menu:bonus", style="primary"),
        InlineKeyboardButton(text="🔥 Задания", callback_data="menu:tasks", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="🌟 Вывести", callback_data="menu:withdraw", style="primary"),
        InlineKeyboardButton(text="💎 Профиль", callback_data="menu:profile", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="🎰 Игры", callback_data="menu:games", style="primary"),
        InlineKeyboardButton(text="👑 Топ", callback_data="menu:top", style="primary"),
    )
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"),
    ]])
