from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def link_button_list_kb(buttons: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for b in buttons:
        builder.row(InlineKeyboardButton(text=f"🔗 {b.label}", callback_data=f"admin:linkbtn_del:{b.id}"))
    builder.row(InlineKeyboardButton(text="➕ Создать кнопку", callback_data="admin:linkbtn_new"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def link_button_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:linkbtn"),
    ]])


def link_button_delete_confirm_kb(link_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:linkbtn_del_confirm:{link_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:linkbtn"),
    ]])
