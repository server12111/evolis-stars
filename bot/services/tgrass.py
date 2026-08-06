import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

TGRASS_API = "https://tgrass.space/offers"
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
                    status = data.get("status", "")

                    # "ok" = подписан на все, "no_offers" = нет подходящих офферов
                    if status in ("ok", "no_offers"):
                        logger.info("TGrass check_tgrass user_id=%d status=%s → 0 offers", user_id, status)
                        return []

                    # "not_ok" = есть неподписанные офферы — возвращаем только их
                    offers = data.get("offers", [])
                    result = []
                    for o in offers:
                        if not o.get("link"):
                            continue
                        # Используем явное сравнение с True, т.к. поле bool по документации
                        # Если поле отсутствует или None — считаем НЕ подписанным (безопасно)
                        if o.get("subscribed") is True:
                            continue
                        result.append({
                            "name": o.get("name") or "Канал",
                            "url": o.get("link", ""),
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
