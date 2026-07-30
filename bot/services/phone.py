"""Phone number allowlist and phone-verification UI helpers."""

import re

from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup


# Country calling codes requested for the initial allowlist:
# CIS countries, USA/NANP (+1), Colombia, Brazil and Myanmar.
DEFAULT_ALLOWED_COUNTRY_CODES = (
    "1",    # USA / NANP (Telegram sends only the calling code)
    "7",    # Russia and Kazakhstan
    "55",   # Brazil
    "57",   # Colombia
    "95",   # Myanmar
    "373",  # Moldova
    "374",  # Armenia
    "375",  # Belarus
    "380",  # Ukraine
    "992",  # Tajikistan
    "993",  # Turkmenistan
    "994",  # Azerbaijan
    "996",  # Kyrgyzstan
    "998",  # Uzbekistan
)

COUNTRY_CODE_NAMES = {
    "1": "США / NANP",
    "7": "Россия / Казахстан",
    "55": "Бразилия",
    "57": "Колумбия",
    "95": "Мьянма",
    "373": "Молдова",
    "374": "Армения",
    "375": "Беларусь",
    "380": "Украина",
    "992": "Таджикистан",
    "993": "Туркменистан",
    "994": "Азербайджан",
    "996": "Кыргызстан",
    "998": "Узбекистан",
}

_PHONE_CHARS = re.compile(r"[^0-9]")


def normalize_phone(phone: str | None) -> str:
    """Return a stable digits-only representation of a Telegram phone."""
    if not phone:
        return ""
    return _PHONE_CHARS.sub("", phone)


def parse_country_codes(raw: str | None) -> tuple[str, ...]:
    """Parse comma/space separated calling codes and return unique codes."""
    codes: set[str] = set()
    for value in re.split(r"[,;\s]+", raw or ""):
        code = re.sub(r"\D", "", value)
        if 1 <= len(code) <= 4:
            codes.add(code)
    return tuple(sorted(codes, key=lambda item: (len(item), item)))


def allowed_country_codes(raw: str | None) -> tuple[str, ...]:
    parsed = parse_country_codes(raw)
    return parsed or DEFAULT_ALLOWED_COUNTRY_CODES


def matching_country_code(phone: str | None, raw_codes: str | None) -> str | None:
    """Find the longest configured calling-code prefix in a phone number."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    codes = allowed_country_codes(raw_codes)
    for code in sorted(codes, key=len, reverse=True):
        if normalized.startswith(code):
            return code
    return None


def phone_request_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажмите кнопку и отправьте контакт",
    )


def phone_check_text() -> str:
    return (
        "📱 <b>Проверка номера</b>\n\n"
        "Чтобы продолжить, поделитесь своим номером кнопкой ниже.\n"
        "Принимаются номера разрешённых стран. Номер нужен только для проверки страны."
    )


def phone_rejected_text() -> str:
    return (
        "❌ Этот номер не подходит для использования бота.\n\n"
        "Разрешены только номера стран из списка допуска. Если это ошибка, отправьте "
        "другой свой номер кнопкой ниже."
    )


async def prompt_phone(message: Message, state: FSMContext) -> None:
    from bot.states.phone import PhoneStates

    if await state.get_state() == PhoneStates.waiting_contact.state:
        return
    await message.answer(phone_check_text(), parse_mode="HTML", reply_markup=phone_request_keyboard())
    await state.set_state(PhoneStates.waiting_contact)
