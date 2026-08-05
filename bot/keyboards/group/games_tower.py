from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def tower_playing_kb(
    max_levels: int,
    mines: list[int],
    history: list[int],
    active_level: int | None,
    coeff: float,
    payout: float,
) -> InlineKeyboardMarkup:
    """active_level is the level still open for picking (None once the round
    has ended — mine hit, top reached, or cashed out). Shows every level at
    once, same as the private-chat Tower game: passed rows revealed
    (mine/pick/empty), the current row clickable, future rows locked."""
    builder = InlineKeyboardBuilder()
    for lvl in range(max_levels - 1, -1, -1):
        if lvl < len(history):
            mine_positions = mines[lvl]
            chosen = history[lvl]
            tiles = []
            for slot in range(3):
                if slot in mine_positions:
                    tiles.append(InlineKeyboardButton(text="💣", callback_data="chattower:noop"))
                elif slot == chosen:
                    tiles.append(InlineKeyboardButton(text="✅", callback_data="chattower:noop"))
                else:
                    tiles.append(InlineKeyboardButton(text="⬜", callback_data="chattower:noop"))
            builder.row(*tiles)
        elif active_level is not None and lvl == active_level:
            builder.row(
                *[InlineKeyboardButton(text="🟩", callback_data=f"chattower:pick:{i}") for i in range(3)]
            )
        else:
            builder.row(*[InlineKeyboardButton(text="🔒", callback_data="chattower:noop") for _ in range(3)])

    if active_level is not None and active_level > 0:
        builder.row(
            InlineKeyboardButton(
                text=f"💰 Забрать {payout:.2f} RP⭐️ (×{coeff:.2f})",
                callback_data="chattower:cashout",
            )
        )
    return builder.as_markup()
