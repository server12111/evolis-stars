from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def stats_scope_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🤖 Статистика бота", callback_data="admin:stats:bot"),
        InlineKeyboardButton(text="💬 Статистика чатов", callback_data="admin:stats:chats"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def stats_back_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ К статистике", callback_data="admin:stats"))
    builder.row(InlineKeyboardButton(text="🏠 Админ-панель", callback_data="admin:main"))
    return builder.as_markup()
