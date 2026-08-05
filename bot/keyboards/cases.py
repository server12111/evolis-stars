from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def cases_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🥉 Бронза (1 RP⭐️)", callback_data="cases:open:1"),
        InlineKeyboardButton(text="🥈 Серебро (3 RP⭐️)", callback_data="cases:open:3"),
    )
    builder.row(InlineKeyboardButton(text="🥇 Золото (5 RP⭐️)", callback_data="cases:open:5"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:games"))
    return builder.as_markup()


def case_confirm_kb(tier: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"✅ Открыть за {tier} RP⭐️", callback_data=f"cases:confirm:{tier}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:cases"),
    )
    return builder.as_markup()


def case_result_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎁 Открыть ещё", callback_data="menu:cases"),
        InlineKeyboardButton(text="🎮 К играм", callback_data="menu:games"),
    )
    return builder.as_markup()
