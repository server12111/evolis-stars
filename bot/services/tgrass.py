import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

TGRASS_API = "https://tgrass.space/offers"
TGRASS_CHECK_API = "https://tgrass.space/check"
_RETRY_DELAYS = [2, 4]


async def check_tgrass(
    user_id: int,
    code: str,
    max_offers: int = 0,
    *,
    is_premium: bool = False,
    username: str | None = None,
    lang: str = "ru",
) -> list[dict] | None:
    """Return unsubscribed channels; return None when the integration is unavailable.

    Docs: https://tgrass.space/integration
    Endpoint: POST /offers
    Response status:
      - "ok"        — user subscribed to all offers (return empty list)
      - "no_offers" — no suitable offers for this user (return empty list)
      - "not_ok"    — some offers remain unsubscribed (return unsubscribed list)
    """
    if not code:
        return []
    body: dict = {
        "tg_user_id": user_id,
        "is_premium": is_premium,
        "lang": lang,
    }
    if username:
        body["tg_login"] = username
    if max_offers > 0:
        body["offers_limit"] = max_offers
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
                async with client.post(
                    TGRASS_API,
                    json=body,
                    headers={"Auth": code, "Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 429:
                        logger.warning("TGrass HTTP 429 (attempt %d), retrying...", attempt + 1)
                        continue
                    if resp.status >= 400:
                        logger.warning("TGrass returned HTTP %s", resp.status)
                        return None
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        logger.warning("TGrass returned malformed data")
                        return None
                    status = data.get("status", "")

                    # "ok" = подписан на все, "no_offers" = нет подходящих офферов
                    if status in ("ok", "no_offers"):
                        logger.info("TGrass check_tgrass user_id=%d status=%s → 0 offers", user_id, status)
                        return []

                    # "not_ok" = есть неподписанные офферы — возвращаем только их
                    offers = data.get("offers", [])
                    if not isinstance(offers, list):
                        logger.warning("TGrass returned invalid offers")
                        return None
                    result = []
                    for o in offers:
                        if not isinstance(o, dict) or not o.get("link"):
                            continue
                        # Используем явное сравнение с True, т.к. поле bool по документации
                        # Если поле отсутствует или None — считаем НЕ подписанным (безопасно)
                        if o.get("subscribed") is True:
                            continue
                        # offer_id becomes this item's `ref` once decorated
                        # (see sponsor_waves._decorate) -- needed so a saved
                        # item that later drops off this list (i.e. the user
                        # is now reported subscribed) can be re-checked for
                        # fraud via check_tgrass_fake(offer_id) below,
                        # instead of only ever being identifiable by URL.
                        result.append({
                            "name": o.get("name") or "Канал",
                            "url": o.get("link", ""),
                            "ref": str(o.get("offer_id", "")),
                        })
                    logger.info("TGrass check_tgrass user_id=%d status=%s → %d offers", user_id, status, len(result))
                    return result
        except Exception as e:
            # Retry (not an immediate give-up) -- a one-off network blip
            # (timeout, connection reset) must not read as "this provider
            # is down" and cascade into an "unavailable" wall for anyone
            # checking in that exact moment, same as the HTTP 429 branch
            # above already does.
            logger.warning("TGrass check error (attempt %d): %s", attempt + 1, e)
    return None


async def check_tgrass_fake(code: str, user_id: int, offer_id: str) -> bool | None:
    """Per-offer fraud check for one sponsor a saved wave item just
    resolved as subscribed on -- the bulk /offers endpoint above never
    reports fraud, only /check does (one offer at a time).

    Docs: https://tgrass.space/integration#check
    Endpoint: POST /check
    Response: {"status": "subscribed"|"not_subscribed"|..., "is_fake": bool}

    Returns None (caller must treat as "unknown, don't flag") when the
    integration is unavailable, the offer_id is missing, or the response
    doesn't parse -- a fraud verdict must never be inferred from a failed
    call, or a transient error would wrongly cost a referrer their reward.
    """
    if not code or not offer_id:
        return None
    try:
        # The API's own docs example shows offer_id as a bare int; the
        # ref is kept as str everywhere internally (same convention as
        # Traffy/FlyerHub refs), so convert only for the wire payload.
        offer_id_int: int | str = int(offer_id)
    except (TypeError, ValueError):
        offer_id_int = offer_id
    body = {"tg_user_id": user_id, "offer_id": offer_id_int}
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
                async with client.post(
                    TGRASS_CHECK_API,
                    json=body,
                    headers={"Auth": code, "Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 429:
                        logger.warning("TGrass /check HTTP 429 (attempt %d), retrying...", attempt + 1)
                        continue
                    if resp.status >= 400:
                        logger.warning("TGrass /check returned HTTP %s", resp.status)
                        return None
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        logger.warning("TGrass /check returned malformed data")
                        return None
                    is_fake = data.get("is_fake")
                    if not isinstance(is_fake, bool):
                        return None
                    return is_fake
        except Exception as e:
            logger.warning("TGrass /check error offer_id=%s (attempt %d): %s", offer_id, attempt + 1, e)
    return None
