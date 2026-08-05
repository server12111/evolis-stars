from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def user_actions_kb(user_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Начислить RP⭐️", callback_data=f"admin:user_add:{user_id}"),
        InlineKeyboardButton(text="➖ Списать RP⭐️", callback_data=f"admin:user_sub:{user_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="👥 Добавить рефералов", callback_data=f"admin:user_refs:{user_id}"),
    )
    if is_blocked:
        builder.row(InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"admin:user_unblock:{user_id}"))
    else:
        builder.row(InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"admin:user_block:{user_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:users"))
    return builder.as_markup()


def users_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin:user_search"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def cancel_kb(back_data: str = "admin:users") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data=back_data),
    ]])
