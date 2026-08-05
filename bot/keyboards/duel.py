from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def duel_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚔️ Создать дуэль", callback_data="duel:create"),
        InlineKeyboardButton(text="🔥 Активные дуэли", callback_data="duel:active"),
    )
    builder.row(InlineKeyboardButton(text="📜 История", callback_data="duel:history"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:games"))
    return builder.as_markup()


def active_duels_kb(duels: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for duel in duels:
        builder.row(InlineKeyboardButton(
            text=f"⚔️ Дуэль #{duel.id} — {float(duel.amount):.0f} RP⭐️",
            callback_data=f"duel:view:{duel.id}",
        ))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="duel:menu"))
    return builder.as_markup()


def duel_view_kb(duel_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⚔️ Вступить в дуэль", callback_data=f"duel:join:{duel_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="duel:active"))
    return builder.as_markup()


def duel_creator_kb(duel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отменить дуэль", callback_data=f"duel:cancel:{duel_id}"),
    ]])


def duel_roll_kb(duel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎲 Бросить кубик", callback_data=f"duel:roll:{duel_id}"),
    ]])


def duel_confirm_kb(duel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"duel:confirm:{duel_id}", style="success"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"duel:decline_join:{duel_id}", style="danger"),
    ]])


def back_to_duel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚔️ К дуэлям", callback_data="duel:menu"),
    ]])
