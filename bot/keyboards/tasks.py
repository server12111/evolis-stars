import hashlib

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def tasks_list_kb(
    tasks: list,
    completed_ids: set,
    pf_uncompleted_count: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for task in tasks:
        done = task.id in completed_ids
        label = f"✅ {task.title}" if done else f"⚡ {task.title} (+{float(task.reward):.1f} ⭐)"
        builder.row(InlineKeyboardButton(
            text=label,
            callback_data=f"task:view:{task.id}" if not done else "task:already_done",
        ))
    if pf_uncompleted_count > 0:
        builder.row(InlineKeyboardButton(
            text=f"🌟 Задания от спонсоров ({pf_uncompleted_count})",
            callback_data="pf_task:start",
        ))
    builder.row(InlineKeyboardButton(text="🔄 Обновить задания", callback_data="menu:tasks"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def task_detail_kb(task_id: int, url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if url:
        builder.row(InlineKeyboardButton(text="➡️ Перейти к заданию", url=url, style="primary"))
    builder.row(InlineKeyboardButton(text="✅ Проверить выполнение", callback_data=f"task:check:{task_id}", style="success"))
    builder.row(
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"task:skip:{task_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="menu:tasks"),
    )
    return builder.as_markup()


def pf_task_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def pf_task_detail_kb(url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    link_key = pf_task_id(url) if url else ""
    if url:
        builder.row(InlineKeyboardButton(text="📲 Подписаться на канал", url=url, style="primary"))
    builder.row(InlineKeyboardButton(text="✅ Проверить выполнение", callback_data=f"pf_task:check:{link_key}", style="success"))
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"),
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"pf_task:skip:{link_key}"),
    )
    return builder.as_markup()


def tasks_not_available_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Проверить подписку", callback_data="sponsor_check"),
    ]])
