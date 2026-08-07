from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.virus_game import AMMO_COST, HERBS_COST, HERBS_SUCCESS_CHANCE, MEDICINE_COST


def virus_ammo_kb(target_id: int, stake: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"🧪 Купить боеприпас ({AMMO_COST:.0f} RP⭐️)",
        callback_data=f"virus:ammo:{target_id}:{stake}",
    ))
    return builder.as_markup()


def virus_cure_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"💊 Лекарство — {MEDICINE_COST:.0f} RP⭐️ (100%)",
        callback_data="virus:cure:medicine",
    ))
    builder.row(InlineKeyboardButton(
        text=f"🌿 Травы — {HERBS_COST:.0f} RP⭐️ ({HERBS_SUCCESS_CHANCE:.0%})",
        callback_data="virus:cure:herbs",
    ))
    return builder.as_markup()
