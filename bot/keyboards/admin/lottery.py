from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def lottery_admin_kb(has_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_active:
        builder.row(
            InlineKeyboardButton(text="🎯 Разыграть сейчас", callback_data="admin:lottery_draw"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="admin:lottery_cancel"),
        )
    else:
        builder.row(InlineKeyboardButton(text="➕ Создать лотерею", callback_data="admin:lottery_new"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:games"))
    return builder.as_markup()


def lottery_end_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎟 По кол-ву билетов", callback_data="lottery_end:tickets"))
    builder.row(InlineKeyboardButton(text="⏰ По времени", callback_data="lottery_end:time"))
    builder.row(InlineKeyboardButton(text="💰 По сумме сбора", callback_data="lottery_end:commission"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin:lottery"))
    return builder.as_markup()
