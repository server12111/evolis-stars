from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def blocked_sponsors_list_kb(entries: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for entry in entries:
        label = entry.url if len(entry.url) <= 40 else entry.url[:37] + "..."
        icon = "🌐" if entry.match_type == "domain" else "🔗"
        builder.row(
            InlineKeyboardButton(text=f"🚫 {icon} {label}", callback_data=f"admin:blocked_sponsor_del:{entry.id}"),
        )
    builder.row(InlineKeyboardButton(text="➕ Заблокировать ссылку", callback_data="admin:blocked_sponsor_new"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def blocked_sponsor_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:blocked_sponsors"),
    ]])


def blocked_sponsor_scope_kb(host: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔗 Только эту ссылку", callback_data="admin:blocked_sponsor_scope:url"),
    )
    if host:
        builder.row(
            InlineKeyboardButton(
                text=f"🌐 Весь домен ({host})", callback_data="admin:blocked_sponsor_scope:domain",
            ),
        )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin:blocked_sponsors"))
    return builder.as_markup()


def blocked_sponsor_delete_confirm_kb(entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Разблокировать", callback_data=f"admin:blocked_sponsor_del_confirm:{entry_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:blocked_sponsors"),
    ]])
