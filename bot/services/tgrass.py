import logging
import aiohttp

logger = logging.getLogger(__name__)

TGRASS_API = "https://tgrass.space/offers"


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
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:
            async with client.post(
                TGRASS_API,
                json=body,
                headers={"Auth": code, "Content-Type": "application/json"},
            ) as resp:
                if resp.status >= 400:
                    logger.warning("TGrass returned HTTP %s", resp.status)
                    return None
                data = await resp.json(content_type=None)
                status = data.get("status", "")

                # "ok" = подписан на все, "no_offers" = нет подходящих офферов
                if status in ("ok", "no_offers"):
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
                return result
    except Exception as e:
        logger.warning("TGrass check error: %s", e)
    return None
