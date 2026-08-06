from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def logs_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📄 Последние 300 строк", callback_data="admin:logs_tail"))
    builder.row(InlineKeyboardButton(text="📎 Весь файл", callback_data="admin:logs_full"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()
