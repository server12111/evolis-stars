import logging
import aiohttp

logger = logging.getLogger(__name__)

PIARFLOW_API = "https://piarflow.com/v1"


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def get_sponsors(
    api_key: str,
    user_id: int,
    chat_id: int,
    max_sponsors: int = 3,
) -> list[dict] | None:
    """Return sponsor tasks, or None when PiarFlow is unavailable."""
    if not api_key:
        return []
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
            async with client.post(
                f"{PIARFLOW_API}/sponsors",
                json={"user_id": user_id, "chat_id": chat_id, "max_sponsors": max_sponsors},
                headers=_headers(api_key),
            ) as resp:
                if resp.status >= 400:
                    logger.warning("PiarFlow get_sponsors HTTP %s", resp.status)
                    return None
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    logger.warning("PiarFlow get_sponsors returned malformed data")
                    return None
                sponsors = data.get("sponsors", [])
                if not isinstance(sponsors, list):
                    logger.warning("PiarFlow get_sponsors returned invalid sponsors")
                    return None
                logger.info("PiarFlow get_sponsors user_id=%d chat_id=%d → %d sponsors", user_id, chat_id, len(sponsors))
                return sponsors
    except Exception as e:
        logger.warning("PiarFlow get_sponsors error: %s", e)
    return None


async def check_sponsors(api_key: str, user_id: int, links: list[str]) -> bool:
    """Check if user subscribed to all given links. Returns True if all subscribed."""
    if not api_key or not links:
        return True
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
            async with client.post(
                f"{PIARFLOW_API}/sponsors/check",
                json={"user_id": user_id, "links": links},
                headers=_headers(api_key),
            ) as resp:
                if resp.status >= 400:
                    logger.warning("PiarFlow check_sponsors HTTP %s", resp.status)
                    return False
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    logger.warning("PiarFlow check_sponsors returned malformed data")
                    return False
                sponsors = data.get("sponsors", [])
                if not isinstance(sponsors, list) or not sponsors:
                    logger.warning("PiarFlow check_sponsors returned no sponsor statuses")
                    return False
                statuses = {
                    str(sponsor.get("link", "")): sponsor.get("status")
                    for sponsor in sponsors
                    if isinstance(sponsor, dict) and sponsor.get("link")
                }
                return all(
                    statuses.get(link) == "subscribed"
                    for link in links
                )
    except Exception as e:
        logger.warning("PiarFlow check_sponsors error: %s", e)
    return False
