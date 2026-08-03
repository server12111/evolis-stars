from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import ChatLinkButton


def broadcast_scope_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💬 В чатах", callback_data="admin:broadcast_scope:chats"),
        InlineKeyboardButton(text="🤖 В боте", callback_data="admin:broadcast_scope:bot"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def broadcast_chat_audience_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📢 Всем чатам", callback_data="admin:broadcast_audience:all"))
    builder.row(InlineKeyboardButton(text="✅ Только с включённой рассылкой", callback_data="admin:broadcast_audience:opted_in"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:broadcast"))
    return builder.as_markup()


def broadcast_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:main"),
    ]])


def broadcast_button_choice_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔗 Прикрепить кнопку", callback_data="admin:broadcast_button:attach"))
    builder.row(InlineKeyboardButton(text="⏭ Без кнопки", callback_data="admin:broadcast_button:skip"))
    return builder.as_markup()


def broadcast_button_pick_kb(buttons: list[ChatLinkButton]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for button in buttons:
        builder.row(InlineKeyboardButton(text=button.label, callback_data=f"admin:broadcast_button_pick:{button.id}"))
    builder.row(InlineKeyboardButton(text="➕ Новая кнопка", callback_data="admin:broadcast_button_new"))
    builder.row(InlineKeyboardButton(text="⏭ Без кнопки", callback_data="admin:broadcast_button:skip"))
    return builder.as_markup()


def broadcast_preview_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Отправить", callback_data="admin:broadcast_confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:main"),
    )
    return builder.as_markup()
