from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def own_sponsors_list_kb(sponsors: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for s in sponsors:
        status = "✅" if s.is_active else "⏸"
        kind_icon = "📢" if s.kind == "channel" else "🤖"
        label = f"{status} {kind_icon} {s.name[:30]} | {s.current_count}/{s.target_count}"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"admin:own_sponsor_view:{s.id}"))
    builder.row(InlineKeyboardButton(text="➕ Добавить спонсора", callback_data="admin:own_sponsor_new"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def own_sponsor_view_kb(sponsor_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "⏸ Отключить" if is_active else "✅ Включить"
    builder.row(
        InlineKeyboardButton(text=toggle_text, callback_data=f"admin:own_sponsor_toggle:{sponsor_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:own_sponsor_del:{sponsor_id}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:own_sponsors"))
    return builder.as_markup()


def own_sponsor_kind_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📢 Канал/чат", callback_data="admin:own_sponsor_kind:channel"),
        InlineKeyboardButton(text="🤖 Бот", callback_data="admin:own_sponsor_kind:bot"),
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin:own_sponsors"))
    return builder.as_markup()


def own_sponsor_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:own_sponsors"),
    ]])


def own_sponsor_delete_confirm_kb(sponsor_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:own_sponsor_del_confirm:{sponsor_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:own_sponsors"),
    ]])
