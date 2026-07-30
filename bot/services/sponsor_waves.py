import json
from dataclasses import dataclass
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import User

WAVE_SIZE = 6
MAX_WAVES = 2

ProviderResult = list[dict] | BaseException
WaveStatus = Literal["pending", "complete", "unavailable"]


@dataclass(slots=True)
class SponsorWaveState:
    status: WaveStatus
    wave: int = 0
    total_waves: int = 0
    items: list[dict] | None = None


def _key(item: dict) -> tuple[str, str]:
    provider = str(item.get("provider", "")).strip().lower()
    url = str(item.get("url", "")).strip().rstrip("/").lower()
    return provider, url


def _url_key(item: dict) -> str:
    return str(item.get("url", "")).strip().rstrip("/").lower()


def _decorate(items: list[dict], provider: str) -> list[dict]:
    result: list[dict] = []
    for item in items:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        result.append(
            {
                "provider": provider,
                "url": url,
                "name": str(item.get("name", "")).strip(),
            }
        )
    return result


def _load(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("url")]


def _dump(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _current_items(user: User) -> list[dict]:
    if user.sponsor_wave == 1:
        return _load(user.sponsor_wave_one)
    if user.sponsor_wave == 2:
        return _load(user.sponsor_wave_two)
    return []


def _total_waves(user: User) -> int:
    if _load(user.sponsor_wave_two):
        return 2
    if _load(user.sponsor_wave_one):
        return 1
    return 0


def initialize_waves(
    user: User,
    *,
    tgrass_result: ProviderResult,
    botohub_result: ProviderResult,
    wave_size: int = WAVE_SIZE,
) -> None:
    """Freeze at most twelve sponsors into two restart-safe waves."""
    if user.sponsor_wave in {1, 2, 3}:
        return

    wave_size = max(1, min(WAVE_SIZE, wave_size))
    combined: list[dict] = []
    if isinstance(botohub_result, list):
        combined.extend(_decorate(botohub_result, "botohub"))
    if isinstance(tgrass_result, list):
        combined.extend(_decorate(tgrass_result, "tgrass"))

    unique: list[dict] = []
    seen_urls: set[str] = set()
    for item in combined:
        url_key = _url_key(item)
        if not url_key or url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        unique.append(item)
        if len(unique) >= wave_size * MAX_WAVES:
            break

    first = unique[:wave_size]
    second = unique[wave_size:wave_size * MAX_WAVES]
    user.sponsor_wave_one = _dump(first) if first else None
    user.sponsor_wave_two = _dump(second) if second else None
    user.sponsor_wave = 1 if first else 3


def evaluate_waves(
    user: User,
    *,
    tgrass_result: ProviderResult,
    botohub_result: ProviderResult,
    wave_size: int = WAVE_SIZE,
) -> SponsorWaveState:
    """Check only saved sponsors and advance through both waves in order."""
    # On the first request every configured provider must answer. Otherwise a
    # temporary outage could freeze waves without that provider's mandatory
    # sponsors and let the user pass them permanently.
    if user.sponsor_wave not in {1, 2, 3} and (
        not isinstance(tgrass_result, list)
        or not isinstance(botohub_result, list)
    ):
        return SponsorWaveState("unavailable")

    initialize_waves(
        user,
        tgrass_result=tgrass_result,
        botohub_result=botohub_result,
        wave_size=wave_size,
    )

    results: dict[str, ProviderResult] = {
        "tgrass": tgrass_result,
        "botohub": botohub_result,
    }

    while user.sponsor_wave in {1, 2}:
        wave = user.sponsor_wave
        saved = _current_items(user)
        if not saved:
            if wave == 1 and _load(user.sponsor_wave_two):
                user.sponsor_wave = 2
                continue
            user.sponsor_wave = 3
            return SponsorWaveState("complete")

        required_providers = {str(item.get("provider", "")) for item in saved}
        if any(
            provider not in results or not isinstance(results[provider], list)
            for provider in required_providers
        ):
            return SponsorWaveState(
                "unavailable",
                wave=wave,
                total_waves=_total_waves(user),
                items=saved,
            )

        unsubscribed: set[tuple[str, str]] = set()
        for provider in required_providers:
            provider_result = results[provider]
            if isinstance(provider_result, list):
                unsubscribed.update(
                    _key(item)
                    for item in _decorate(provider_result, provider)
                )

        remaining = [item for item in saved if _key(item) in unsubscribed]
        if remaining:
            return SponsorWaveState(
                "pending",
                wave=wave,
                total_waves=_total_waves(user),
                items=remaining[:WAVE_SIZE],
            )

        if wave == 1 and _load(user.sponsor_wave_two):
            user.sponsor_wave = 2
            continue

        user.sponsor_wave = 3
        return SponsorWaveState("complete")

    return SponsorWaveState("complete")


def sponsor_wave_markup(items: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(text="Подписаться", url=str(item["url"]))
        for item in items[:WAVE_SIZE]
        if item.get("url")
    ]
    for index in range(0, len(buttons), 2):
        builder.row(*buttons[index:index + 2])
    builder.row(
        InlineKeyboardButton(
            text="Я подписался на все каналы",
            callback_data="sponsor_check",
        )
    )
    return builder.as_markup()


def sponsor_wave_text(wave: int, total_waves: int) -> str:
    return (
        "📣 <b>Подписка на спонсоров</b>\n\n"
        "Подпишись на все каналы ниже, затем нажми "
        "<b>«Я подписался на все каналы»</b>."
    )
