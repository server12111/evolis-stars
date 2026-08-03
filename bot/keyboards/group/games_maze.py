from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def maze_playing_kb(can_cashout: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_cashout:
        builder.row(
            InlineKeyboardButton(text="➡️ Идти дальше", callback_data="maze:continue"),
            InlineKeyboardButton(text="💰 Забрать", callback_data="maze:cashout"),
        )
    else:
        builder.row(InlineKeyboardButton(text="➡️ Идти дальше", callback_data="maze:continue"))
    return builder.as_markup()
