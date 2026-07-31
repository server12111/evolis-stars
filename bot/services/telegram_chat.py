import re
from typing import Any
from urllib.parse import urlparse

_PUBLIC_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_TELEGRAM_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}


def telegram_chat_id(value: str | int | None) -> str | int | None:
    """Convert a public Telegram link to a Bot API compatible chat ID."""
    if isinstance(value, int):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lstrip("-").isdigit():
        return int(raw)
    if raw.startswith("@") and _PUBLIC_USERNAME.fullmatch(raw[1:]):
        return raw

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.hostname and parsed.hostname.lower() in _TELEGRAM_HOSTS:
        username = parsed.path.strip("/").split("/", 1)[0]
        if (
            username
            and username not in ("joinchat", "c")
            and not username.startswith("+")
            and _PUBLIC_USERNAME.fullmatch(username)
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
