from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def owner_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎟 Промокод", callback_data="chatmenu:promo"),
        InlineKeyboardButton(text="🎁 Бонус", callback_data="chatmenu:bonus"),
    )
    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="chatmenu:refresh"))
    return builder.as_markup()
