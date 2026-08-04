from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

REGISTRATION_REQUIRED_TEXT = (
    "Чтобы пользоваться ботом в группе, сначала пройдите регистрацию в личных сообщениях."
)


def registration_required_kb(bot_username: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Пройти регистрацию",
            url=f"https://t.me/{bot_username}?start=group",
        )
    )
    return builder.as_markup()
