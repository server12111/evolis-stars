from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def vc_withdraw_amounts_kb(amounts: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(text=f"💎 {a // 1000}к VC", callback_data=f"vcwithdraw:amount:{a}")
        for a in amounts
    ]
    for i in range(0, len(buttons), 2):
        builder.row(*buttons[i:i + 2])
    builder.row(InlineKeyboardButton(text="✏️ Ввести сумму", callback_data="vcwithdraw:amount:custom"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:withdraw"))
    return builder.as_markup()


def vc_withdraw_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:withdraw"),
    ]])


def vc_withdraw_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="vcwithdraw:confirm", style="success"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="menu:withdraw", style="danger"),
    ]])


def vc_withdraw_subscribe_kb(link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📢 Подписаться", url=link))
    builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="vcwithdraw:check_sub"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:withdraw"))
    return builder.as_markup()


def admin_vc_withdraw_kb(withdrawal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"admin:vcwithdraw_approve:{withdrawal_id}", style="success"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin:vcwithdraw_reject:{withdrawal_id}", style="danger"),
    ]])
