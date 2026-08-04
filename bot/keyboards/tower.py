from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def tower_bet_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    bets = [1, 3, 5, 10, 25]
    btns = [InlineKeyboardButton(text=f"{b} ⭐", callback_data=f"tower:bet:{b}") for b in bets]
    for i in range(0, len(btns), 3):
        builder.row(*btns[i:i+3])
    builder.row(InlineKeyboardButton(text="✏️ Своя сумма", callback_data="tower:bet:custom"))
    builder.row(InlineKeyboardButton(text="◀️ Казино", callback_data="menu:casino"))
    return builder.as_markup()


def tower_playing_kb(
    level: int,
    max_levels: int,
    mines: list[int],
    history: list[int],
    coeff: float,
    payout: float,
    bet: float,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # Render top-down: highest level first
    for lvl in range(max_levels - 1, -1, -1):
        if lvl > level:
            # Future level — locked
            builder.row(
                InlineKeyboardButton(text="🔒", callback_data="tower:noop"),
                InlineKeyboardButton(text="🔒", callback_data="tower:noop"),
                InlineKeyboardButton(text="🔒", callback_data="tower:noop"),
            )
        elif lvl == level:
            # Current level — clickable
            builder.row(
                InlineKeyboardButton(text="🟩", callback_data="tower:pick:0"),
                InlineKeyboardButton(text="🟩", callback_data="tower:pick:1"),
                InlineKeyboardButton(text="🟩", callback_data="tower:pick:2"),
            )
        else:
            # Passed level — show mine position(s)
            mine_positions = mines[lvl]
            chosen = history[lvl]
            tiles = []
            for slot in range(3):
                if slot in mine_positions:
                    tiles.append(InlineKeyboardButton(text="💣", callback_data="tower:noop"))
                elif slot == chosen:
                    tiles.append(InlineKeyboardButton(text="✅", callback_data="tower:noop"))
                else:
                    tiles.append(InlineKeyboardButton(text="⬜", callback_data="tower:noop"))
            builder.row(*tiles)

    if level > 0:
        builder.row(
            InlineKeyboardButton(
                text=f"💰 Забрать {payout:.2f} ⭐ (×{coeff:.2f})",
                callback_data="tower:cashout",
            ),
        )
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="tower:quit"))
    return builder.as_markup()


def tower_over_kb(won: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Играть снова", callback_data="menu:tower"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def tower_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:casino"),
    ]])
