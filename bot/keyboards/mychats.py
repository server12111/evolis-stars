from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import Chat, ChatBroadcastMessage

# Per https://core.telegram.org/api/links, admin= rights are joined with
# "+". Minimal set the bot actually needs: delete spam, manage restricted/
# banned members, share the invite link, pin announcements, and moderate
# member tags — matches profile.py's "Добавить в группу" link exactly.
_ADD_GROUP_ADMIN_RIGHTS = "delete_messages+restrict_members+invite_users+pin_messages+manage_tags"


def _add_group_url(bot_username: str) -> str:
    return f"https://t.me/{bot_username}?startgroup=owner&admin={_ADD_GROUP_ADMIN_RIGHTS}"


def mychats_list_kb(chats: list[Chat], bot_username: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats:
        label = chat.title or f"Чат {chat.chat_id}"
        builder.row(InlineKeyboardButton(text=f"💬 {label}", callback_data=f"mychats:open:{chat.chat_id}"))
    builder.row(InlineKeyboardButton(text="➕ Подключить ещё чат", url=_add_group_url(bot_username)))
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
    builder.row(InlineKeyboardButton(text="📢 Своя рассылка", callback_data=f"mychats:custombc:{chat_id}"))
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


def custom_broadcast_panel_kb(
    chat_id: int, messages: list[ChatBroadcastMessage], enabled: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, message in enumerate(messages, start=1):
        builder.row(
            InlineKeyboardButton(
                text=f"🗑 Удалить текст #{i}",
                callback_data=f"mychats:custombc:del:{chat_id}:{message.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="➕ Добавить текст", callback_data=f"mychats:custombc:add:{chat_id}"))
    builder.row(
        InlineKeyboardButton(text="⏱ Задать интервал", callback_data=f"mychats:custombc:interval:{chat_id}"),
    )
    toggle_label = "❌ Выключить рассылку" if enabled else "✅ Включить рассылку"
    builder.row(InlineKeyboardButton(text=toggle_label, callback_data=f"mychats:custombc:toggle:{chat_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"mychats:open:{chat_id}"))
    return builder.as_markup()


def custom_broadcast_cancel_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"mychats:custombc:{chat_id}"),
    ]])


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
    builder.row(InlineKeyboardButton(text="➕ Подключить ещё чат", url=_add_group_url(bot_username)))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()
