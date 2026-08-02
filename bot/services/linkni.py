import logging

import aiohttp

logger = logging.getLogger(__name__)

LINKNI_SUBSCRIPTIONS_API = "https://go.linkni.me/api/subscriptions"
LINKNI_SUB_CODE = "tasks"


def linkni_link(code: str) -> str:
    return f"https://telegram.me/linknibot/app?startapp=x_{code}_{LINKNI_SUB_CODE}"


async def check_linkni_subscription(code: str, user_id: int) -> str | None:
    """Query linkni for this user's most recent subscription record in the
    last hour. Returns 'subscribed' / 'not_subscribed' / 'no_sponsors', or
    None if there's no record yet (user hasn't visited) or the API failed."""
    if not code:
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
            async with client.get(
                LINKNI_SUBSCRIPTIONS_API,
                params={"code": code, "user_id": user_id},
            ) as resp:
                if resp.status >= 400:
                    logger.warning("linkni subscriptions HTTP %s", resp.status)
                    return None
                data = await resp.json(content_type=None)
                if not isinstance(data, list) or not data:
                    return None
                latest = max(data, key=lambda item: str(item.get("timestamp", "")))
                status = latest.get("status")
                return str(status) if status else None
    except Exception as exc:
        logger.warning("linkni subscriptions error: %s", exc)
        return None
