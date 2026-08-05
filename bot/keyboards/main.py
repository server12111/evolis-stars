from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb(has_chats: bool = False) -> InlineKeyboardMarkup:
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
    builder.row(
        InlineKeyboardButton(text="🎲 Рандом", callback_data="menu:random", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="🪙 Донат", callback_data="menu:exchange", style="success"),
    )
    # Only shown to users who own at least one connected chat — everyone
    # else has nothing to manage there.
    if has_chats:
        builder.row(
            InlineKeyboardButton(text="💬 Панель чатов", callback_data="mychats:list", style="primary"),
        )
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"),
    ]])
