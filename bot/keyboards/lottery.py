from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def lottery_menu_kb(can_buy: bool, ticket_price: float = 5.0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_buy:
        builder.row(InlineKeyboardButton(
            text=f"🎟 Купить билет ({ticket_price:.0f} RP⭐️)",
            callback_data="game:lottery_buy",
            style="success",
        ))
    builder.row(InlineKeyboardButton(text="🎮 К играм", callback_data="menu:games"))
    return builder.as_markup()


def admin_lottery_kb(has_active: bool, has_participants: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_active:
        if has_participants:
            builder.row(
                InlineKeyboardButton(text="🎲 Случайный розыгрыш", callback_data="admin:lottery_random"),
                InlineKeyboardButton(text="👆 Выбрать победителя", callback_data="admin:lottery_pick"),
            )
        builder.row(InlineKeyboardButton(text="❌ Отменить лотерею", callback_data="admin:lottery_cancel"))
    else:
        builder.row(InlineKeyboardButton(text="➕ Создать лотерею", callback_data="admin:lottery_new"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def admin_lottery_end_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎟 По кол-ву билетов", callback_data="admin:lottery_end:tickets"),
        InlineKeyboardButton(text="📅 По дате", callback_data="admin:lottery_end:time"),
    )
    builder.row(InlineKeyboardButton(text="💰 По сумме сборов", callback_data="admin:lottery_end:commission"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin:lottery"))
    return builder.as_markup()


def admin_lottery_skip_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏩ Пропустить", callback_data="admin:lottery_skip"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:lottery"),
    )
    return builder.as_markup()


def admin_lottery_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Запустить", callback_data="admin:lottery_create:confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:lottery"),
    )
    return builder.as_markup()


def admin_lottery_pick_kb(participants: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for uid, cnt in participants:
        builder.row(InlineKeyboardButton(
            text=f"ID:{uid} — {cnt} билет(ов)",
            callback_data=f"admin:lottery_winner:{uid}",
        ))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:lottery"))
    return builder.as_markup()
