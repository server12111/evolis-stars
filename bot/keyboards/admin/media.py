from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

KEY_LABELS = {
    "welcome": "👋 Приветствие",
    "main_menu": "🏠 Главное меню",
    "earn": "💸 Заработать",
    "withdraw": "RP⭐️ Вывод",
    "bonus": "🎁 Бонус",
    "tasks": "📋 Задания",
    "games": "🎮 Игры",
    "profile": "👤 Профиль",
    "top": "🏆 Топ",
    "tos": "📜 Соглашение",
}

_CASE_PRIZES_ALL = sorted({
    0.1, 0.3, 0.5, 0.7, 0.9, 1.0, 1.2, 1.4, 1.5, 1.6, 1.8,
    2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 6.5, 7.0, 7.5, 8.0, 9.0,
})

VIDEO_LABELS: dict[str, str] = {
    f"case_video_{str(p).replace('.', '_')}": f"🎁 Приз {p} RP⭐️"
    for p in _CASE_PRIZES_ALL
}
VIDEO_LABELS["wheel_video_50x"] = "🎰 Колесо: Джекпот (50x)"
VIDEO_LABELS["wheel_video_01x"] = "🎰 Колесо: Проигрыш (0.1x)"


def media_list_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, label in KEY_LABELS.items():
        builder.row(InlineKeyboardButton(text=label, callback_data=f"admin:media_edit:{key}"))
    builder.row(InlineKeyboardButton(text="🎬 Видео", callback_data="admin:video_list"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def video_list_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    prize_keys = [k for k in VIDEO_LABELS if k.startswith("case_video_")]
    wheel_keys = [k for k in VIDEO_LABELS if k.startswith("wheel_")]
    # Prize buttons in pairs
    for i in range(0, len(prize_keys), 2):
        row = [
            InlineKeyboardButton(text=VIDEO_LABELS[k], callback_data=f"admin:video_upload:{k}")
            for k in prize_keys[i:i+2]
        ]
        builder.row(*row)
    # Wheel buttons
    for k in wheel_keys:
        builder.row(InlineKeyboardButton(text=VIDEO_LABELS[k], callback_data=f"admin:video_upload:{k}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:media"))
    return builder.as_markup()


def media_edit_kb(key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"admin:media_text:{key}"),
        InlineKeyboardButton(text="🖼 Изменить фото", callback_data=f"admin:media_photo:{key}"),
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Сбросить текст на дефолт", callback_data=f"admin:media_reset:{key}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:media"))
    return builder.as_markup()


def media_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:media"),
    ]])
