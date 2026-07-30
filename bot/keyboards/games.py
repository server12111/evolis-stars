from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

GAME_TYPES = ["football", "basketball", "bowling", "dice", "slots", "darts"]

GAME_LABELS = {
    "football":   "Футбол ⚽",
    "basketball": "Баскетбол 🏀",
    "bowling":    "Боулинг 🎳",
    "dice":       "Кубики 🎲",
    "slots":      "Слоты 🎰",
    "darts":      "Дартс 🎯",
}


def games_menu_kb(configs: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    enabled = [g for g in GAME_TYPES if configs.get(g, {}).get("enabled")]
    # 2 per row for game buttons
    btns = []
    for game in enabled:
        cfg = configs[game]
        coeff_label = cfg.get("coeff_label", "")
        btns.append(InlineKeyboardButton(
            text=f"{GAME_LABELS[game]} | {coeff_label}",
            callback_data=f"game:play:{game}",
        ))
    for i in range(0, len(btns), 2):
        builder.row(*btns[i:i+2])

    builder.row(
        InlineKeyboardButton(text="🎟 Лотерея", callback_data="game:lottery"),
        InlineKeyboardButton(text="⚔️ Дуэль", callback_data="duel:menu"),
    )
    builder.row(InlineKeyboardButton(text="🎰 Казино (Мины / Башня / Кейсы)", callback_data="menu:casino"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def casino_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎡 Все или ничего", callback_data="menu:wheel"),
        InlineKeyboardButton(text="🎁 Кейсы", callback_data="menu:cases"),
    )
    builder.row(
        InlineKeyboardButton(text="💣 Мины", callback_data="menu:mines"),
        InlineKeyboardButton(text="🗼 Башня", callback_data="menu:tower"),
    )
    builder.row(InlineKeyboardButton(text="🏺 Аукцион", callback_data="menu:auction"))
    builder.row(InlineKeyboardButton(text="◀️ К играм", callback_data="menu:games"))
    return builder.as_markup()


def dice_side_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📈 Больше 3", callback_data="game:dice:high"),
        InlineKeyboardButton(text="📉 Меньше 4", callback_data="game:dice:low"),
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:games"))
    return builder.as_markup()


def football_side_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚽ Гол (x1.5)", callback_data="game:football:goal"),
        InlineKeyboardButton(text="🥅 Промах (x2.2)", callback_data="game:football:miss"),
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:games"))
    return builder.as_markup()


def basketball_side_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🏀 Чистый гол (x4)", callback_data="game:basketball:clean"),
        InlineKeyboardButton(text="🏀 Любой гол (x2.2)", callback_data="game:basketball:any"),
    )
    builder.row(
        InlineKeyboardButton(text="😬 Застрял (x4)", callback_data="game:basketball:stuck"),
        InlineKeyboardButton(text="❌ Промах (x1.5)", callback_data="game:basketball:miss"),
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:games"))
    return builder.as_markup()


def bowling_side_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎳 Страйк (x5)", callback_data="game:bowling:strike"),
        InlineKeyboardButton(text="❌ Промах (x4)", callback_data="game:bowling:miss"),
    )
    builder.row(InlineKeyboardButton(text="🎳 Попал (x2)", callback_data="game:bowling:partial"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:games"))
    return builder.as_markup()


def darts_side_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎯 В центр (x5)", callback_data="game:darts:center"),
        InlineKeyboardButton(text="💨 Отскок (x5)", callback_data="game:darts:bounce"),
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:games"))
    return builder.as_markup()


def game_result_kb(game_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Ещё раз", callback_data=f"game:play:{game_type}"),
        InlineKeyboardButton(text="🎮 К играм", callback_data="menu:games"),
    )
    return builder.as_markup()


def game_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:games"),
    ]])
