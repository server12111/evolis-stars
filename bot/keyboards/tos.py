from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _doc_buttons(builder: InlineKeyboardBuilder, user_agreement_url: str, privacy_policy_url: str) -> None:
    builder.row(InlineKeyboardButton(text="📜 Пользовательское соглашение", url=user_agreement_url))
    builder.row(InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=privacy_policy_url))


def tos_accept_kb(user_agreement_url: str, privacy_policy_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _doc_buttons(builder, user_agreement_url, privacy_policy_url)
    builder.row(InlineKeyboardButton(text="✅ Принимаю", callback_data="tos_accept"))
    return builder.as_markup()


def tos_view_kb(user_agreement_url: str, privacy_policy_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _doc_buttons(builder, user_agreement_url, privacy_policy_url)
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:profile"))
    return builder.as_markup()
