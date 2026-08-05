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


def bonus_sponsor_deeplink_kb(bot_username: str, sponsor_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    param = "startchannel" if sponsor_type == "channel" else "startgroup"
    text = "➕ Добавить канал" if sponsor_type == "channel" else "➕ Добавить чат"
    # &admin= (no specific rights) forces Telegram's "add as administrator"
    # flow instead of "add as member" — without it, ?startgroup= links add
    # the bot as a plain member for groups, and get_chat_member is
    # unreliable for a non-admin bot, so the subscription check would
    # silently never work.
    builder.row(InlineKeyboardButton(text=text, url=f"https://t.me/{bot_username}?{param}=addsponsor&admin="))
    return builder.as_markup()


def bonus_subscribe_kb(sponsors: list[ChatBonusSponsor], bonus_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sponsor in sponsors:
        label = f"📢 {sponsor.title or sponsor.username or 'Спонсор'}"
        if sponsor.username:
            builder.row(InlineKeyboardButton(text=label, url=f"https://t.me/{sponsor.username}"))
    builder.row(InlineKeyboardButton(text="✅ Проверить", callback_data=f"chatbonus:check:{bonus_id}"))
    return builder.as_markup()
