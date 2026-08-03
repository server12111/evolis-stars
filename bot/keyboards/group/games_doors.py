from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def doors_pick_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [InlineKeyboardButton(text=f"🚪 {i + 1}", callback_data=f"doors:pick:{i}") for i in range(4)]
    builder.row(*buttons)
    return builder.as_markup()


def doors_result_kb(is_final_level: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = [InlineKeyboardButton(text="💰 Забрать", callback_data="doors:cashout")]
    if not is_final_level:
        row.append(InlineKeyboardButton(text="➡️ Дальше", callback_data="doors:next"))
    builder.row(*row)
    return builder.as_markup()
