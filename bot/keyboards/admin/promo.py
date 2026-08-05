from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def promo_list_kb(promos: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in promos:
        label = f"🎟 {p.code} | {float(p.reward_amount):.1f}RP⭐️ | {p.used_count}/{p.usage_limit or '∞'}"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"admin:promo_del:{p.id}"))
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin:promo_new"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def promo_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:promo"),
    ]])


def promo_delete_confirm_kb(promo_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:promo_del_confirm:{promo_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:promo"),
    ]])
