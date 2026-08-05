from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def auction_kb(has_active: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_active:
        builder.row(
            InlineKeyboardButton(text="➕ 1 RP⭐️", callback_data="auction:bid:1"),
            InlineKeyboardButton(text="➕ 5 RP⭐️", callback_data="auction:bid:5"),
        )
        builder.row(
            InlineKeyboardButton(text="➕ 10 RP⭐️", callback_data="auction:bid:10"),
            InlineKeyboardButton(text="➕ 25 RP⭐️", callback_data="auction:bid:25"),
        )
        builder.row(InlineKeyboardButton(text="✏️ Своя сумма", callback_data="auction:bid:custom"))
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="auction:refresh"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"),
    )
    return builder.as_markup()


def auction_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:auction"),
    ]])
