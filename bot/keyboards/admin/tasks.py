from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def tasks_list_kb(tasks: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tasks:
        status = "✅" if t.is_active else "❌"
        builder.row(InlineKeyboardButton(
            text=f"{status} {t.title[:30]} | {float(t.reward):.1f}RP⭐️",
            callback_data=f"admin:task_view:{t.id}",
        ))
    builder.row(InlineKeyboardButton(text="➕ Создать задание", callback_data="admin:task_new"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def task_view_kb(task_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "❌ Отключить" if is_active else "✅ Включить"
    builder.row(
        InlineKeyboardButton(text=toggle_text, callback_data=f"admin:task_toggle:{task_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:task_del:{task_id}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:tasks"))
    return builder.as_markup()


def task_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:tasks"),
    ]])
