import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

logger = logging.getLogger(__name__)

# Central catalog supplied by the bot owner.
SETTINGS = "5870982283724328568"
PROFILE = "5870994129244131212"
PEOPLE = "5870772616305839506"
PERSON_APPROVED = "5891207662678317861"
PERSON_REJECTED = "5893192487324880883"
FILE = "5870528606328852614"
SMILE = "5870764288364252592"
GROWTH = "5870930636742595124"
STATISTICS = "5870921681735781843"
HOME = "5873147866364514353"
LOCKED = "6037249452824072506"
UNLOCKED = "6037496202990194718"
MEGAPHONE = "6039422865189638057"
CHECK = "5870633910337015697"
CROSS = "5870657884844462243"
PENCIL = "5870676941614354370"
TRASH = "5870875489362513438"
DOWN = "5893057118545646106"
PAPERCLIP = "6039451237743595514"
LINK = "5769289093221454192"
INFO = "6028435952299413210"
BOT_ICON = "6030400221232501136"
EYE = "6037397706505195857"
HIDDEN = "6037243349675544634"
UPLOAD = "5963103826075456248"
DOWNLOAD = "6039802767931871481"
NOTIFICATION = "6039486778597970865"
GIFT = "6032644646587338669"
CLOCK = "5983150113483134607"
CELEBRATION = "6041731551845159060"
WRITE = "5870753782874246579"
MEDIA = "6035128606563241721"
LOCATION = "6042011682497106307"
WALLET = "5769126056262898415"
BOX = "5884479287171485878"
CRYPTOBOT = "5260752406890711732"
CALENDAR = "5890937706803894250"
TAG = "5886285355279193209"
ELAPSED = "5775896410780079073"
APPS = "5778672437122045013"
BRUSH = "6050679691004612757"
ADD_TEXT = "5771851822897566479"
FORMAT = "5778479949572738874"
MONEY = "5904462880941545555"
SEND_MONEY = "5890848474563352982"
RECEIVE_MONEY = "5879814368572478751"
CODE = "5940433880585605708"
LOADING = "5345906554510012647"
SUBSCRIBE = "6039450962865688331"
VERIFY = "5774022692642492953"

_MESSAGE_EMOJI_IDS: dict[str, str] = {
    "⚙️": SETTINGS,
    "⚙": SETTINGS,
    "👤": PROFILE,
    "👥": PEOPLE,
    "📁": FILE,
    "🙂": SMILE,
    "📊": STATISTICS,
    "📈": GROWTH,
    "🏘": HOME,
    "🏠": HOME,
    "🔒": LOCKED,
    "🔓": UNLOCKED,
    "📣": MEGAPHONE,
    "📢": MEGAPHONE,
    "✅": CHECK,
    "❌": CROSS,
    "🖋": PENCIL,
    "✏️": PENCIL,
    "✏": PENCIL,
    "🗑": TRASH,
    "📎": PAPERCLIP,
    "🔗": LINK,
    "ℹ️": INFO,
    "ℹ": INFO,
    "🤖": BOT_ICON,
    "👁": EYE,
    "⬆️": UPLOAD,
    "⬆": UPLOAD,
    "⬇️": DOWNLOAD,
    "⬇": DOWNLOAD,
    "🔔": NOTIFICATION,
    "🎁": GIFT,
    "⏰": CLOCK,
    "🎉": CELEBRATION,
    "✍️": WRITE,
    "✍": WRITE,
    "🖼": MEDIA,
    "📍": LOCATION,
    "👛": WALLET,
    "📦": BOX,
    "👾": CRYPTOBOT,
    "📅": CALENDAR,
    "🏷": TAG,
    "🕓": ELAPSED,
    "🖌": BRUSH,
    "↔️": FORMAT,
    "↔": FORMAT,
    "🪙": MONEY,
    "🔨": CODE,
    "🔄": LOADING,
}
_MESSAGE_EMOJI_RE = re.compile(
    "|".join(
        re.escape(symbol)
        for symbol in sorted(_MESSAGE_EMOJI_IDS, key=len, reverse=True)
    )
)

_BUTTON_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("провер", VERIFY),
    ("подпис", SUBSCRIBE),
    ("настрой", SETTINGS),
    ("профил", PROFILE),
    ("номер", PERSON_APPROVED),
    ("пользовател", PEOPLE),
    ("реферал", PEOPLE),
    ("статист", STATISTICS),
    ("топ", GROWTH),
    ("главное меню", HOME),
    ("меню", HOME),
    ("рассыл", MEGAPHONE),
    ("уведом", NOTIFICATION),
    ("задани", FILE),
    ("медиа", MEDIA),
    ("фото", MEDIA),
    ("ссыл", LINK),
    ("промокод", GIFT),
    ("бонус", GIFT),
    ("подар", GIFT),
    ("вывести", SEND_MONEY),
    ("выплат", RECEIVE_MONEY),
    ("баланс", WALLET),
    ("игр", APPS),
    ("казино", APPS),
    ("обнов", LOADING),
    ("ещё раз", LOADING),
    ("играть снова", LOADING),
    ("принять", CHECK),
    ("запустить", CHECK),
    ("подтверд", CHECK),
    ("отмен", CROSS),
    ("отклон", CROSS),
    ("удал", TRASH),
    ("измен", PENCIL),
    ("своя сумма", PENCIL),
    ("создать", ADD_TEXT),
    ("добав", ADD_TEXT),
    ("скач", DOWNLOAD),
    ("отправ", UPLOAD),
    ("дата", CALENDAR),
    ("времен", CLOCK),
    ("истори", ELAPSED),
    ("заработ", MONEY),
)

_VISUAL_BUTTON_PREFIXES = (
    "captcha:",
    "mines:open:",
    "mines:noop",
    "tower:pick:",
    "tower:noop",
)
_CUSTOM_TAG_RE = re.compile(
    r'<tg-emoji\s+emoji-id="\d+">.*?</tg-emoji>',
    flags=re.DOTALL,
)
_CUSTOM_TAG_UNWRAP_RE = re.compile(
    r'<tg-emoji\s+emoji-id="\d+">(.*?)</tg-emoji>',
    flags=re.DOTALL,
)


def premium(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'


def decorate_message_text(text: str) -> str:
    """Replace known ordinary emoji outside existing custom-emoji tags."""
    parts: list[str] = []
    cursor = 0
    for match in _CUSTOM_TAG_RE.finditer(text):
        parts.append(_decorate_plain_segment(text[cursor:match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_decorate_plain_segment(text[cursor:]))
    return "".join(parts)


def _decorate_plain_segment(text: str) -> str:
    return _MESSAGE_EMOJI_RE.sub(
        lambda match: premium(
            match.group(0),
            _MESSAGE_EMOJI_IDS[match.group(0)],
        ),
        text,
    )


def _strip_ordinary_emoji(text: str) -> str:
    result: list[str] = []
    for char in text:
        codepoint = ord(char)
        if (
            unicodedata.category(char) == "So"
            or codepoint in {0x200D, 0x20E3, 0xFE0E, 0xFE0F}
            or 0x1F3FB <= codepoint <= 0x1F3FF
        ):
            continue
        result.append(char)
    return re.sub(r"\s{2,}", " ", "".join(result)).strip()


def _button_icon(text: str) -> str | None:
    normalized = text.replace("\ufe0f", "").strip()
    for symbol, emoji_id in sorted(
        _MESSAGE_EMOJI_IDS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if normalized.startswith(symbol.replace("\ufe0f", "")):
            return emoji_id
    lowered = normalized.lower()
    for keyword, emoji_id in _BUTTON_KEYWORDS:
        if keyword in lowered:
            return emoji_id
    return None


def _decorate_button(button: InlineKeyboardButton | KeyboardButton) -> None:
    callback_data = getattr(button, "callback_data", None) or ""
    if callback_data.startswith(_VISUAL_BUTTON_PREFIXES):
        return
    if not button.icon_custom_emoji_id:
        button.icon_custom_emoji_id = _button_icon(button.text)
    clean_text = _strip_ordinary_emoji(button.text)
    if clean_text:
        button.text = clean_text


def decorate_markup(markup: Any) -> None:
    if isinstance(markup, InlineKeyboardMarkup):
        for row in markup.inline_keyboard:
            for button in row:
                _decorate_button(button)
    elif isinstance(markup, ReplyKeyboardMarkup):
        for row in markup.keyboard:
            for button in row:
                _decorate_button(button)


def _remove_custom_emoji(method: TelegramMethod[Any]) -> None:
    for field_name in ("text", "caption"):
        value = getattr(method, field_name, None)
        if isinstance(value, str):
            setattr(
                method,
                field_name,
                _CUSTOM_TAG_UNWRAP_RE.sub(r"\1", value),
            )
    markup = getattr(method, "reply_markup", None)
    if isinstance(markup, InlineKeyboardMarkup):
        rows = markup.inline_keyboard
    elif isinstance(markup, ReplyKeyboardMarkup):
        rows = markup.keyboard
    else:
        rows = []
    for row in rows:
        for button in row:
            button.icon_custom_emoji_id = None


class PremiumEmojiMiddleware:
    async def __call__(
        self,
        make_request: Callable[
            [Bot, TelegramMethod[Any]],
            Awaitable[Any],
        ],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        changed = False
        for field_name in ("text", "caption"):
            value = getattr(method, field_name, None)
            if isinstance(value, str):
                decorated = decorate_message_text(value)
                if decorated != value:
                    setattr(method, field_name, decorated)
                    changed = True

        markup = getattr(method, "reply_markup", None)
        if isinstance(markup, (InlineKeyboardMarkup, ReplyKeyboardMarkup)):
            decorate_markup(markup)
            changed = True

        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            premium_error = any(
                marker in error_text
                for marker in (
                    "custom emoji",
                    "custom_emoji",
                    "icon_custom_emoji_id",
                    "button_type_invalid",
                )
            )
            if not changed or not premium_error:
                raise
            logger.warning(
                "Telegram rejected custom emoji for %s; retrying without premium entities",
                type(method).__name__,
            )
            _remove_custom_emoji(method)
            return await make_request(bot, method)
