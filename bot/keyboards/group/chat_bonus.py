from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import ChatBonusSponsor
from bot.database.repositories.chat_bonus import MAX_BONUS_SPONSORS


def bonus_mode_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎁 Обычный бонус", callback_data="chatbonus:mode:self_serve"),
        InlineKeyboardButton(text="🏆 Конкурс", callback_data="chatbonus:mode:contest"),
    )
    return builder.as_markup()


def bonus_sponsors_kb(sponsor_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if sponsor_count < MAX_BONUS_SPONSORS:
        builder.row(InlineKeyboardButton(text="📢 Добавить каналы", callback_data="chatbonus:addsponsor:channel"))
        builder.row(InlineKeyboardButton(text="👥 Добавить чаты", callback_data="chatbonus:addsponsor:chat"))
    builder.row(InlineKeyboardButton(text="✅ Готово", callback_data="chatbonus:sponsors:done"))
    return builder.as_markup()


# Per https://core.telegram.org/api/links — admin= rights are joined with
# "+" (confirmed live). Exact toggle sets picked and confirmed live via
# Telegram's own "Add Bot as Admin" dialog for each chat type.
_CHANNEL_SPONSOR_ADMIN_RIGHTS = (
    "post_messages+edit_messages+delete_messages+"
    "post_stories+edit_stories+delete_stories+"
    "invite_users+manage_direct_messages"
)
_CHAT_SPONSOR_ADMIN_RIGHTS = "delete_messages+invite_users+pin_messages+manage_tags+anonymous"


def bonus_sponsor_deeplink_kb(bot_username: str, sponsor_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    param = "startchannel" if sponsor_type == "channel" else "startgroup"
    text = "➕ Добавить канал" if sponsor_type == "channel" else "➕ Добавить чат"
    rights = _CHANNEL_SPONSOR_ADMIN_RIGHTS if sponsor_type == "channel" else _CHAT_SPONSOR_ADMIN_RIGHTS
    builder.row(InlineKeyboardButton(
        text=text, url=f"https://t.me/{bot_username}?{param}=addsponsor&admin={rights}",
    ))
    return builder.as_markup()


def bonus_subscribe_kb(sponsors: list[ChatBonusSponsor], bonus_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sponsor in sponsors:
        label = f"📢 {sponsor.title or sponsor.username or 'Спонсор'}"
        if sponsor.username:
            builder.row(InlineKeyboardButton(text=label, url=f"https://t.me/{sponsor.username}"))
    builder.row(InlineKeyboardButton(text="✅ Проверить", callback_data=f"chatbonus:check:{bonus_id}"))
    return builder.as_markup()
