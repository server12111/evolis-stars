from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def wheel_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎡 Крутить колесо", callback_data="wheel:choose_bet"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:games"))
    return builder.as_markup()


def wheel_bet_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for amt in [1, 5, 10, 25, 50]:
        builder.button(text=f"{amt} RP⭐️", callback_data=f"wheel:bet:{amt}")
    builder.adjust(3, 2)
    builder.row(InlineKeyboardButton(text="✏️ Своя сумма", callback_data="wheel:bet:custom"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:wheel"))
    return builder.as_markup()


def wheel_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:wheel"),
    ]])


def wheel_result_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Крутить ещё", callback_data="wheel:choose_bet"),
        InlineKeyboardButton(text="🎮 К играм", callback_data="menu:games"),
    )
    return builder.as_markup()
