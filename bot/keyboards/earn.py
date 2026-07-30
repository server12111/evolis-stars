from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def earn_kb(
    bot_username: str,
    user_id: int,
    returnable_count: int = 0,
) -> InlineKeyboardMarkup:
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="🚀 Поделиться ссылкой",
        url=f"https://t.me/share/url?url={ref_link}&text=Присоединяйся+к+боту!",
    ))
    builder.row(
        InlineKeyboardButton(
            text=f"♻️ Вернуть рефералов ({returnable_count})",
            callback_data="earn:returns:0",
        )
    )
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def return_referrals_kb(
    usernames: list[str],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for username in usernames:
        builder.row(
            InlineKeyboardButton(
                text=f"✉️ Написать @{username}",
                url=f"https://t.me/{username}",
            )
        )

    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"earn:returns:{page - 1}",
            )
        )
    if page + 1 < total_pages:
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"earn:returns:{page + 1}",
            )
        )
    if navigation:
        builder.row(*navigation)
    builder.row(
        InlineKeyboardButton(
            text="◀️ К реферальной ссылке",
            callback_data="menu:earn",
        )
    )
    return builder.as_markup()
