from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def blocked_sponsors_list_kb(entries: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for entry in entries:
        label = entry.url if len(entry.url) <= 45 else entry.url[:42] + "..."
        builder.row(
            InlineKeyboardButton(text=f"🚫 {label}", callback_data=f"admin:blocked_sponsor_del:{entry.id}"),
        )
    builder.row(InlineKeyboardButton(text="➕ Заблокировать ссылку", callback_data="admin:blocked_sponsor_new"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def blocked_sponsor_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:blocked_sponsors"),
    ]])


def blocked_sponsor_delete_confirm_kb(entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Разблокировать", callback_data=f"admin:blocked_sponsor_del_confirm:{entry_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:blocked_sponsors"),
    ]])
