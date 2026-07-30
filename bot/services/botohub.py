import logging
import aiohttp

logger = logging.getLogger(__name__)

BOTOHUB_API = "https://botohub.me/get-tasks"


async def check_botohub(user_id: int, api_key: str) -> list[dict] | None:
    """Return unsubscribed channels; return None when the integration is unavailable."""
    if not api_key:
        return []
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:
            async with client.post(
                BOTOHUB_API,
                json={"chat_id": user_id},
                headers={"Auth": api_key, "Content-Type": "application/json"},
            ) as resp:
                if resp.status >= 400:
                    logger.warning("BotoHub returned HTTP %s", resp.status)
                    return None
                data = await resp.json(content_type=None)
                if data.get("completed") or data.get("skip"):
                    return []
                tasks = data.get("tasks", [])
                return [
                    {"name": "Канал", "url": url}
                    for url in tasks
                    if url
                ]
    except Exception as e:
        logger.warning("BotoHub check error: %s", e)
    return None
