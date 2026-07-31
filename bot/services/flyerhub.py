import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

FLYERHUB_API = "https://api.flyerhubs.com"
_RETRY_DELAYS = [1.0, 2.0]


async def fh_get_tasks(
    api_key: str,
    user_id: int,
    chat_id: int,
    user_locale: str = "ru",
    limit: int = 5,
) -> list[dict] | None:
    """Return tasks from FlyerHub, or None on API failure. Empty list if no tasks."""
    if not api_key:
        return []
    
    payload = {
        "key": api_key,
        "chat_id": chat_id,
        "user_id": user_id,
        "user_locale": user_locale,
        "limit": limit
    }
    
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
                async with client.post(
                    f"{FLYERHUB_API}/max/get_tasks",
                    json=payload,
                ) as resp:
                    if resp.status == 429:
                        logger.warning("FlyerHub get_tasks HTTP 429 (attempt %d), retrying...", attempt + 1)
                        continue
                    if resp.status == 404:
                        logger.debug("FlyerHub get_tasks HTTP 404 (no tasks)")
                        return []
                    if resp.status >= 400:
                        logger.warning("FlyerHub get_tasks HTTP %s", resp.status)
                        return None
                    
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        logger.warning("FlyerHub get_tasks returned malformed data")
                        return None
                    
                    if "error" in data and data["error"]:
                        logger.warning("FlyerHub get_tasks returned error: %s", data["error"])
                        if "Not found" in str(data["error"]) or "no tasks" in str(data["error"]).lower():
                            return []
                        return None

                    tasks = data.get("result", [])
                    if tasks is None:
                        return []
                    if not isinstance(tasks, list):
                        logger.warning("FlyerHub get_tasks returned invalid result")
                        return None
                    
                    logger.info("FlyerHub get_tasks user_id=%d → %d tasks", user_id, len(tasks))
                    return tasks

        except TimeoutError:
            logger.warning("FlyerHub get_tasks TimeoutError (attempt %d)", attempt + 1)
        except aiohttp.ClientError as exc:
            logger.warning("FlyerHub get_tasks ClientError: %s", exc)
        except Exception as exc:
            logger.warning("FlyerHub get_tasks Error: %s", exc)
            
    return None


async def fh_check_task(api_key: str, signature: str) -> str | None:
    """Check task status by signature. 
    Returns 'complete', 'incomplete', 'waiting', 'abort', 'unavailable' or None on failure."""
    if not api_key or not signature:
        return "incomplete"

    payload = {
        "key": api_key,
        "signature": signature,
    }
    
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
                async with client.post(
                    f"{FLYERHUB_API}/max/check_task",
                    json=payload,
                ) as resp:
                    if resp.status == 429:
                        logger.warning("FlyerHub check_task HTTP 429 (attempt %d), retrying...", attempt + 1)
                        continue
                    if resp.status >= 400:
                        logger.warning("FlyerHub check_task HTTP %s", resp.status)
                        return None
                    
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        logger.warning("FlyerHub check_task returned malformed data")
                        return None
                    
                    if "error" in data and data["error"]:
                        logger.warning("FlyerHub check_task returned error: %s", data["error"])
                        return None

                    result = data.get("result")
                    return str(result) if result else None

        except TimeoutError:
            logger.warning("FlyerHub check_task TimeoutError (attempt %d)", attempt + 1)
        except aiohttp.ClientError as exc:
            logger.warning("FlyerHub check_task ClientError: %s", exc)
        except Exception as exc:
            logger.warning("FlyerHub check_task Error: %s", exc)
            
    return None
