import re
from typing import Any
from urllib.parse import urlparse

from aiogram import Bot

_PUBLIC_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_TELEGRAM_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
# Reserved t.me path prefixes that are NOT a channel/group username -- e.g.
# a folder invite link (t.me/addlist/<slug>) or a boost link
# (t.me/boost/<username>). Without this exclusion the first path segment
# would be misread as a literal channel username and get_chat_member would
# be called against a chat that doesn't exist (or, in the worst case, one
# that coincidentally does).
_RESERVED_PATH_SEGMENTS = {"joinchat", "c", "addlist", "boost", "s", "iv", "share", "proxy"}


def _is_bot_username(username: str) -> bool:
    # Telegram enforces this at registration time -- every bot username
    # must end in "bot" (case-insensitive), no exceptions.
    return username.lower().endswith("bot")


def telegram_chat_id(value: str | int | None) -> str | int | None:
    """Convert a public Telegram link to a Bot API compatible chat ID.

    Deliberately returns None for anything that looks like a bot username:
    get_chat_member only works on chats (channels/groups) with a member
    list -- calling it against a bot always fails, so a bot-type sponsor
    can never be independently verified this way. Callers that gate
    "keep this sponsor pending unless we can positively confirm
    otherwise" on telegram_chat_id() returning non-None would otherwise
    keep reinstating a bot sponsor forever, even after the provider has
    already correctly confirmed it (see _reinstate_expired_pinned_
    sponsors) -- there's simply no live check to fall back to here, so
    the provider's own report has to be trusted as-is, same as any other
    unverifiable (private-invite, web) sponsor.
    """
    if isinstance(value, int):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lstrip("-").isdigit():
        return int(raw)
    if raw.startswith("@") and _PUBLIC_USERNAME.fullmatch(raw[1:]) and not _is_bot_username(raw[1:]):
        return raw

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.hostname and parsed.hostname.lower() in _TELEGRAM_HOSTS:
        username = parsed.path.strip("/").split("/", 1)[0]
        if (
            username
            and username.lower() not in _RESERVED_PATH_SEGMENTS
            and not username.startswith("+")
            and _PUBLIC_USERNAME.fullmatch(username)
            and not _is_bot_username(username)
        ):
            return f"@{username}"
    return None


def is_subscribed(member: Any) -> bool:
    raw_status = getattr(member, "status", "")
    status = str(getattr(raw_status, "value", raw_status)).lower()
    if status in {"left", "kicked", "banned"}:
        return False
    if status == "restricted" and not bool(getattr(member, "is_member", False)):
        return False
    return bool(status)


async def is_bot_admin_in_chat(bot: Bot, raw_target: str) -> bool:
    """Verify the bot itself is an admin in the given chat/channel — required
    before relying on get_chat_member to check OTHER users' subscriptions
    there (an unprivileged bot gets unreliable/empty results)."""
    chat_id = telegram_chat_id(raw_target)
    if chat_id is None:
        return False
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
    except Exception:
        return False
    raw_status = getattr(member, "status", "")
    status = str(getattr(raw_status, "value", raw_status)).lower()
    return status in {"administrator", "creator"}
