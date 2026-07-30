import logging
import aiohttp

logger = logging.getLogger(__name__)

TGRASS_API = "https://tgrass.space/offers"


async def check_tgrass(user_id: int, code: str, max_offers: int = 0) -> list[dict] | None:
    """Return unsubscribed channels; return None when the integration is unavailable."""
    if not code:
        return []
    body: dict = {"tg_user_id": user_id, "is_premium": False, "lang": "ru"}
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
                if status == "no_offers":
                    return []
                offers = data.get("offers", [])
                return [
                    {"name": o.get("name") or "Канал", "url": o.get("link", "")}
                    for o in offers
                    if not o.get("subscribed") and o.get("link")
                ]
    except Exception as e:
        logger.warning("TGrass check error: %s", e)
    return None
