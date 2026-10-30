from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import Gift
from aiogram.utils.keyboard import InlineKeyboardBuilder


def gift_list_kb(gifts: list[Gift]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for gift in gifts:
        emoji = gift.sticker.emoji if gift.sticker and gift.sticker.emoji else "🎁"
        builder.button(text=f"{emoji} {gift.star_count}⭐️", callback_data=f"admin:gift_pick:{gift.id}")
    builder.adjust(3)
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def gift_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌️ Отмена", callback_data="admin:gift_cancel"),
    ]])


def gift_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✔️ Отправить", callback_data="admin:gift_send")],
        [InlineKeyboardButton(text="❌️ Отмена", callback_data="admin:gift_cancel")],
    ])
