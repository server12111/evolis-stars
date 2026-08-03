from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def bonus_mode_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎁 Обычный бонус", callback_data="chatbonus:mode:self_serve"),
        InlineKeyboardButton(text="🏆 Конкурс", callback_data="chatbonus:mode:contest"),
    )
    return builder.as_markup()
