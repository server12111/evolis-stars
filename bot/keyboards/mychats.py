from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import Chat


def mychats_list_kb(chats: list[Chat], bot_username: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats:
        label = chat.title or f"Чат {chat.chat_id}"
        builder.row(InlineKeyboardButton(text=f"💬 {label}", callback_data=f"mychats:open:{chat.chat_id}"))
    builder.row(InlineKeyboardButton(
        text="➕ Подключить ещё чат",
        url=(
            f"https://t.me/{bot_username}?startgroup=owner"
            "&admin=change_info+delete_messages+invite_users+restrict_members+"
            "pin_messages+manage_topics+promote_members+manage_video_chats+anonymous+manage_chat"
        ),
    ))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def mychat_panel_kb(chat_id: int, broadcast_opt_in: bool, has_promo: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_promo:
        builder.row(InlineKeyboardButton(text="🎁 Бонус", callback_data=f"mychats:bonus:{chat_id}"))
    else:
        builder.row(
            InlineKeyboardButton(text="🎟 Промокод", callback_data=f"mychats:promo:{chat_id}"),
            InlineKeyboardButton(text="🎁 Бонус", callback_data=f"mychats:bonus:{chat_id}"),
        )
    broadcast_label = "📣 Рассылка: ✅ вкл" if broadcast_opt_in else "📣 Рассылка: ❌ выкл"
    builder.row(InlineKeyboardButton(text=broadcast_label, callback_data=f"mychats:broadcast:{chat_id}"))
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data=f"mychats:stats:{chat_id}"),
        InlineKeyboardButton(text="👑 Топ", callback_data=f"mychats:top:{chat_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🎰 Игры", callback_data=f"mychats:games:{chat_id}"),
        InlineKeyboardButton(text="🎰 Лог рулетки", callback_data=f"mychats:log:{chat_id}"),
    )
    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data=f"mychats:refresh:{chat_id}"))
    builder.row(InlineKeyboardButton(text="◀️ К списку чатов", callback_data="mychats:list"))
    return builder.as_markup()


def mychat_back_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"mychats:open:{chat_id}"),
    ]])


def mychat_back_to_list_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ К списку чатов", callback_data="mychats:list"),
    ]])


def connected_instructions_kb(bot_username: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Открыть панель чатов", callback_data="mychats:list"))
    builder.row(InlineKeyboardButton(
        text="➕ Подключить ещё чат",
        url=(
            f"https://t.me/{bot_username}?startgroup=owner"
            "&admin=change_info+delete_messages+invite_users+restrict_members+"
            "pin_messages+manage_topics+promote_members+manage_video_chats+anonymous+manage_chat"
        ),
    ))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()
