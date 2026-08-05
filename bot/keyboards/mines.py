from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def mines_bet_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    bets = [1, 3, 5, 10, 25]
    btns = [InlineKeyboardButton(text=f"{b} RP⭐️", callback_data=f"mines:bet:{b}") for b in bets]
    for i in range(0, len(btns), 3):
        builder.row(*btns[i:i+3])
    builder.row(InlineKeyboardButton(text="✏️ Своя сумма", callback_data="mines:bet:custom"))
    builder.row(InlineKeyboardButton(text="◀️ Казино", callback_data="menu:casino"))
    return builder.as_markup()


def mines_count_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    counts = [(3, "3 мины"), (5, "5 мин"), (10, "10 мин"), (15, "15 мин")]
    for count, label in counts:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"mines:count:{count}"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="menu:casino"))
    return builder.as_markup()


def mines_board_kb(board: list[int], opened: list[int], game_over: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = []
    for i in range(25):
        if i in opened:
            if board[i] == 1:
                text = "💣"
            else:
                text = "💎"
            btn = InlineKeyboardButton(text=text, callback_data="mines:noop")
        elif game_over:
            text = "💣" if board[i] == 1 else "⬜"
            btn = InlineKeyboardButton(text=text, callback_data="mines:noop")
        else:
            btn = InlineKeyboardButton(text="⬜", callback_data=f"mines:open:{i}")
        row.append(btn)
        if len(row) == 5:
            builder.row(*row)
            row = []
    return builder


def mines_playing_kb(board: list[int], opened: list[int], coeff: float, payout: float) -> InlineKeyboardMarkup:
    builder = mines_board_kb(board, opened)
    builder.row(
        InlineKeyboardButton(
            text=f"💰 Забрать {payout:.2f} RP⭐️ (×{coeff:.2f})",
            callback_data="mines:cashout",
        ),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="mines:quit"),
    )
    return builder.as_markup()


def mines_over_kb(board: list[int], opened: list[int]) -> InlineKeyboardMarkup:
    builder = mines_board_kb(board, opened, game_over=True)
    builder.row(InlineKeyboardButton(text="🔄 Играть снова", callback_data="menu:mines"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def mines_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:casino"),
    ]])
