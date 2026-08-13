import json
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import User

WAVE_SIZE = 10
MAX_WAVE_SIZE = 20
MAX_WAVES = 1

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


def normalize_sponsor_url(url: str) -> str:
    """Canonical form used both for in-wave dedup and for matching against
    the admin blocklist (BlockedSponsorRepository stores/compares the same
    way) -- keep these in sync."""
    return str(url or "").strip().rstrip("/").lower()


def _url_key(item: dict) -> str:
    return normalize_sponsor_url(item.get("url", ""))


def extract_host(url: str) -> str:
    """Bare lowercase hostname, e.g. "api.flyerpartners.com" -- used for
    domain-level blocklist entries. Some providers (FlyerHub "follow link"
    tasks in particular) hand out a freshly-signed tracking URL
    (?sign=...) every single time the same underlying sponsor is offered,
    so an exact-URL block can never match twice; blocking by host covers
    every signed variant at once."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").lower()


def is_sponsor_blocked(url: str, blocked_urls: frozenset[str], blocked_domains: frozenset[str]) -> bool:
    if not blocked_urls and not blocked_domains:
        return False
    if normalize_sponsor_url(url) in blocked_urls:
        return True
    return bool(blocked_domains) and extract_host(url) in blocked_domains


def classify_sponsor_type(url: str) -> Literal["tg", "web"]:
    """Classify a sponsor link as a Telegram resource or a web/other link."""
    url_key = str(url or "").strip().lower()
    if "t.me/" in url_key or "telegram.me/" in url_key or "telegram.dog/" in url_key:
        return "tg"
    return "web"


def _decorate(items: list[dict], provider: str) -> list[dict]:
    result: list[dict] = []
    for item in items:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        decorated = {
            "provider": provider,
            "url": url,
            "name": str(item.get("name", "")).strip(),
        }
        # ref: an opaque provider-side id (Traffy's assignment_id, FlyerHub's
        # signature) needed to re-check this exact item later instead of
        # diffing a freshly re-fetched batch -- see sponsor_wall.py's
        # traffy/flyerhub recheck path.
        ref = str(item.get("ref", "")).strip()
        if ref:
            decorated["ref"] = ref
        # kind="trust" marks an item our own bot must NEVER independently
        # clear via get_chat_member (e.g. a FlyerHub "give boost"/"follow
        # link" task) -- membership doesn't mean the actual action was
        # done, so only the provider's own check verdict may resolve it.
        # Default ("verify", implicit when absent) is a real channel
        # subscription, eligible for the usual live cross-check.
        if str(item.get("kind", "")) == "trust":
            decorated["kind"] = "trust"
        result.append(decorated)
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


def wave_in_progress(user: User) -> bool:
    """True while a wave is genuinely frozen mid-progress right now (1 or
    2) -- as opposed to 0 (never started) or 3 (fully resolved). NOT the
    same signal as sponsors_verified, which sponsor_recheck_loop flips back
    to False for every verified user every ~10 minutes regardless of
    whether a wave is actually pending."""
    return user.sponsor_wave in (1, 2)


def total_sponsor_count(user: User) -> int:
    """Total sponsors across every wave the user was shown — used for the
    "you subscribed to N sponsors" confirmation message."""
    return len(_load(user.sponsor_wave_one)) + len(_load(user.sponsor_wave_two))


def initialize_waves(
    user: User,
    *,
    tgrass_result: ProviderResult,
    botohub_result: ProviderResult,
    traffy_result: ProviderResult = None,
    flyerhub_result: ProviderResult = None,
    wave_size: int | None = None,
    blocked_urls: frozenset[str] = frozenset(),
    blocked_domains: frozenset[str] = frozenset(),
) -> None:
    """Freeze at most twelve sponsors into two restart-safe waves.

    Provider priority when trimming to wave_size * MAX_WAVES: Traffy,
    FlyerHub, TgRass, Botohub -- e.g. Traffy contributing 10 and FlyerHub 2
    fills a 12-sponsor wave before TgRass/Botohub are even considered.
    Telegram-resource sponsors are still sorted before web/other ones
    regardless of provider (see the sort below).

    blocked_urls/blocked_domains are dropped before the wave_size trim --
    an admin-blocked sponsor a provider keeps offering must never get
    frozen into a wave in the first place. See is_sponsor_blocked."""
    if user.sponsor_wave in {1, 2}:
        return

    if wave_size is None:
        wave_size = WAVE_SIZE
    wave_size = max(1, min(MAX_WAVE_SIZE, wave_size))
    combined: list[dict] = []
    if isinstance(traffy_result, list):
        combined.extend(_decorate(traffy_result, "traffy"))
    if isinstance(flyerhub_result, list):
        combined.extend(_decorate(flyerhub_result, "flyerhub"))
    if isinstance(tgrass_result, list):
        combined.extend(_decorate(tgrass_result, "tgrass"))
    if isinstance(botohub_result, list):
        combined.extend(_decorate(botohub_result, "botohub"))

    unique: list[dict] = []
    seen_urls: set[str] = set()
    for item in combined:
        url_key = _url_key(item)
        if not url_key or url_key in seen_urls or is_sponsor_blocked(url_key, blocked_urls, blocked_domains):
            continue
        seen_urls.add(url_key)
        item["type"] = classify_sponsor_type(url_key)
        unique.append(item)
        if len(unique) >= wave_size * MAX_WAVES:
            break

    # Telegram-resource sponsors are shown first, web/other sponsors last —
    # regardless of which provider supplied them.
    unique.sort(key=lambda x: 0 if x.get("type") == "tg" else 1)

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
    traffy_result: ProviderResult = None,
    flyerhub_result: ProviderResult = None,
    traffy_configured: bool = False,
    flyerhub_configured: bool = False,
    wave_size: int | None = None,
    top_up: bool = True,
    blocked_urls: frozenset[str] = frozenset(),
    blocked_domains: frozenset[str] = frozenset(),
) -> SponsorWaveState:
    """Check only saved sponsors and advance through both waves in order.

    top_up=False disables growing the wave with replacement sponsors (see
    below) without disabling anything else — needed by the "skip sponsors"
    price quote, which must report what's genuinely still pending right
    now without side-effect-persisting a bigger wave than what's on
    screen just because someone opened the price dialog.

    blocked_urls: see initialize_waves' own docstring -- also auto-
    resolves a blocked sponsor still sitting in an ALREADY-frozen wave
    (blocked after that wave was assembled), the same way a retired
    "piarflow" item is auto-resolved below, and is excluded from top-up
    candidates so a blocked sponsor can never re-enter as a replacement."""
    if wave_size is None:
        wave_size = WAVE_SIZE

    # On the first request every configured provider must answer. Otherwise a
    # temporary outage could freeze waves without that provider's mandatory
    # sponsors and let the user pass them permanently.
    if user.sponsor_wave not in {1, 2, 3} and (
        not isinstance(tgrass_result, list)
        or not isinstance(botohub_result, list)
        or (traffy_configured and not isinstance(traffy_result, list))
        or (flyerhub_configured and not isinstance(flyerhub_result, list))
    ):
        return SponsorWaveState("unavailable")

    initialize_waves(
        user,
        tgrass_result=tgrass_result,
        botohub_result=botohub_result,
        traffy_result=traffy_result,
        flyerhub_result=flyerhub_result,
        wave_size=wave_size,
        blocked_urls=blocked_urls,
        blocked_domains=blocked_domains,
    )

    results: dict[str, ProviderResult] = {
        "tgrass": tgrass_result,
        "botohub": botohub_result,
        "traffy": traffy_result,
        "flyerhub": flyerhub_result,
    }

    while user.sponsor_wave in {1, 2}:
        wave = user.sponsor_wave
        # "piarflow" is a retired ОП provider (kept only for the separate
        # "Задания" feature, see tasks.py) -- it will never again appear in
        # `results` above. A user whose wave was frozen with a piarflow
        # sponsor before this change must not get stuck returning
        # "unavailable" forever; auto-resolve any leftover piarflow items
        # instead, same as if the user had finished them. Same auto-resolve
        # for a sponsor an admin blocked after this wave was already frozen.
        saved = [
            item for item in _current_items(user)
            if str(item.get("provider", "")) != "piarflow"
            and not is_sponsor_blocked(item.get("url", ""), blocked_urls, blocked_domains)
        ]
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

        # Top up: a sponsor that just got confirmed drops out of `remaining`
        # above, but the user should see a replacement instead of a
        # shrinking wave (same for a wave that started under wave_size
        # because a provider simply didn't have enough at freeze time).
        # Candidates come straight from `results` — the same fresh,
        # already-verified "still unsubscribed" lists used above, so a
        # replacement is never one the user is secretly already
        # subscribed to. Never re-offer anything already in `saved`
        # (already shown once this wave, whether resolved or still
        # pending) or already picked as a replacement. Dedup by URL alone
        # (matching initialize_waves' own dedup), not the provider-scoped
        # _key() — the same URL reported by two different providers is
        # still the same sponsor, and must not be topped up as "new" just
        # because it's attributed to a different provider than the one
        # already saved under.
        if top_up and len(remaining) < wave_size:
            already_shown = {_url_key(item) for item in saved}
            picked = {_url_key(item) for item in remaining}
            new_candidates: list[dict] = []
            for provider, provider_result in results.items():
                if len(remaining) >= wave_size:
                    break
                if not isinstance(provider_result, list):
                    continue
                for candidate in _decorate(provider_result, provider):
                    if len(remaining) >= wave_size:
                        break
                    url_key = _url_key(candidate)
                    if (
                        not url_key
                        or url_key in already_shown
                        or url_key in picked
                        or is_sponsor_blocked(url_key, blocked_urls, blocked_domains)
                    ):
                        continue
                    new_candidates.append(candidate)
                    remaining.append(candidate)
                    picked.add(url_key)

            if new_candidates:
                # Append to the FULL saved history (resolved items
                # included), not just `remaining` — referral-reward
                # sponsor counting (_current_sponsor_urls / total_sponsor_
                # count) reads sponsor_wave_one/two directly and needs
                # every sponsor ever offered, not only the ones still
                # pending right now.
                persisted = saved + new_candidates
                if wave == 1:
                    user.sponsor_wave_one = _dump(persisted)
                else:
                    user.sponsor_wave_two = _dump(persisted)

        if remaining:
            return SponsorWaveState(
                "pending",
                wave=wave,
                total_waves=_total_waves(user),
                items=remaining[:wave_size],
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
        for item in items
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
    builder.row(
        InlineKeyboardButton(
            text="⏭ Скипнуть спонсоров",
            callback_data="sponsor_skip",
        )
    )
    return builder.as_markup()


def skip_current_wave(user: User) -> SponsorWaveState:
    """Force-completes the currently active wave without checking any
    subscription — used when the user pays real Telegram Stars to skip it
    instead of subscribing. Mirrors evaluate_waves' own advance-or-complete
    branch (the one taken when a genuine re-check finds nothing left
    unsubscribed), just triggered by a successful payment instead."""
    if user.sponsor_wave == 1 and _load(user.sponsor_wave_two):
        user.sponsor_wave = 2
        return SponsorWaveState(
            "pending",
            wave=2,
            total_waves=_total_waves(user),
            items=_load(user.sponsor_wave_two)[:WAVE_SIZE],
        )
    user.sponsor_wave = 3
    return SponsorWaveState("complete")


def sponsor_wave_text(wave: int, total_waves: int) -> str:
    progress = f" — шаг {wave} из {total_waves}" if total_waves > 1 else ""
    return (
        f"📣 <b>Подписка на спонсоров</b>{progress}\n\n"
        "Подпишись на все каналы ниже, затем нажми "
        "<b>«Я подписался на все каналы»</b>.\n\n"
        "Это откроет доступ к боту — а если ты пришёл по реферальной "
        "ссылке, пригласивший тебя получит награду."
    )
