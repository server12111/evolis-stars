from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def crypto_method_choice_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💵 USDT (через @send)", callback_data="cryptowithdraw:method:usdt_send"))
    builder.row(InlineKeyboardButton(text="💎 TON", callback_data="cryptowithdraw:method:ton"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:withdraw"))
    return builder.as_markup()


def crypto_withdraw_amounts_kb(amounts: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(text=f"{a} RP⭐️", callback_data=f"cryptowithdraw:amount:{a}")
        for a in amounts
    ]
    for i in range(0, len(buttons), 2):
        builder.row(*buttons[i:i + 2])
    builder.row(InlineKeyboardButton(text="✏️ Ввести сумму", callback_data="cryptowithdraw:amount:custom"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:withdraw"))
    return builder.as_markup()


def crypto_withdraw_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:withdraw"),
    ]])


def crypto_withdraw_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="cryptowithdraw:confirm", style="success"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="menu:withdraw", style="danger"),
    ]])


def admin_crypto_withdraw_kb(withdrawal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"admin:cryptowithdraw_approve:{withdrawal_id}", style="success"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin:cryptowithdraw_reject:{withdrawal_id}", style="danger"),
    ]])
