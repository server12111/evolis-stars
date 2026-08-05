from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def withdraw_amounts_kb(amounts: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(text=f"💫 {a} ⭐", callback_data=f"withdraw:amount:{a}", style="success")
        for a in amounts
    ]
    for i in range(0, len(buttons), 2):
        builder.row(*buttons[i:i+2])
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def withdraw_captcha_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:withdraw"),
    ]])


def withdraw_recipient_choice_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Себе", callback_data="withdraw:recipient:self"))
    builder.row(InlineKeyboardButton(text="🎁 Другому пользователю", callback_data="withdraw:recipient:other"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:withdraw"))
    return builder.as_markup()


def withdraw_recipient_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:withdraw"),
    ]])


def withdraw_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="withdraw:confirm", style="success"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="menu:withdraw", style="danger"),
    ]])


def payments_channel_kb(link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📢 Канал выплат", url=link))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def admin_withdraw_kb(withdrawal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"admin:withdraw_approve:{withdrawal_id}", style="success"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin:withdraw_reject:{withdrawal_id}", style="danger"),
    ]])
