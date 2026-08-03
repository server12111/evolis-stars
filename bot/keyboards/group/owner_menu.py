from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def owner_menu_kb(broadcast_opt_in: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎟 Промокод", callback_data="chatmenu:promo"),
        InlineKeyboardButton(text="🎁 Бонус", callback_data="chatmenu:bonus"),
    )
    broadcast_label = "📣 Рассылка: ✅ вкл" if broadcast_opt_in else "📣 Рассылка: ❌ выкл"
    builder.row(InlineKeyboardButton(text=broadcast_label, callback_data="chatmenu:broadcast_toggle"))
    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="chatmenu:refresh"))
    return builder.as_markup()
