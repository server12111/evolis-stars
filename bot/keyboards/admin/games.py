from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

GAME_TYPES = ["football", "basketball", "bowling", "dice", "slots", "darts"]
GAME_LABELS_SHORT = {
    "football": "⚽ Футбол",
    "basketball": "🏀 Баскетбол",
    "bowling": "🎳 Боулинг",
    "dice": "🎲 Кубики",
    "slots": "🎰 Слоты",
    "darts": "🎯 Дартс",
}


def games_admin_kb(configs: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for game in GAME_TYPES:
        enabled = configs.get(game, {}).get("enabled", True)
        status = "✅" if enabled else "❌"
        builder.row(InlineKeyboardButton(
            text=f"{status} {GAME_LABELS_SHORT[game]}",
            callback_data=f"admin:game_cfg:{game}",
        ))
    builder.row(
        InlineKeyboardButton(text="🎡 Колесо", callback_data="admin:game_cfg:wheel"),
        InlineKeyboardButton(text="🎁 Кейсы", callback_data="admin:game_cfg:cases"),
    )
    builder.row(InlineKeyboardButton(text="🎟 Лотерея", callback_data="admin:lottery"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"))
    return builder.as_markup()


def game_config_kb(game: str, enabled: bool, show_tunables: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle = "❌ Отключить" if enabled else "✅ Включить"
    builder.row(InlineKeyboardButton(text=toggle, callback_data=f"admin:game_toggle:{game}"))
    if show_tunables:
        builder.row(InlineKeyboardButton(text="⚙️ Изменить коэффициенты", callback_data=f"admin:game_coeffs:{game}"))
        builder.row(InlineKeyboardButton(text="📊 Мин. ставка", callback_data=f"admin:game_minbet:{game}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin:games"))
    return builder.as_markup()


def game_coeffs_kb(game: str, coeff_keys: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for suffix, label in coeff_keys:
        builder.row(InlineKeyboardButton(
            text=label,
            callback_data=f"admin:game_coef_pick:{game}:{suffix}",
        ))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin:game_cfg:{game}"))
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:games"),
    ]])
