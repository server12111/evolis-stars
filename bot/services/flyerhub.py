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


async def fh_get_completed_tasks(api_key: str, user_id: int) -> dict | None:
    """Return {'completed_tasks': [...], 'count_all_tasks': int} for the user,
    or None on API failure. Some FlyerHub bot keys are registered as a
    "webapp" type — /get_tasks and /check_task are prohibited for that type
    (tasks are discovered and completed entirely inside FlyerHub's own
    Telegram Mini App), so /get_completed_tasks is the only way to learn
    what the user has finished and needs to be paid for."""
    if not api_key:
        return {"completed_tasks": [], "count_all_tasks": 0}

    payload = {"key": api_key, "user_id": user_id}

    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
                async with client.post(
                    f"{FLYERHUB_API}/get_completed_tasks",
                    json=payload,
                ) as resp:
                    if resp.status == 429:
                        logger.warning("FlyerHub get_completed_tasks HTTP 429 (attempt %d), retrying...", attempt + 1)
                        continue
                    if resp.status >= 400:
                        logger.warning("FlyerHub get_completed_tasks HTTP %s", resp.status)
                        return None

                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        logger.warning("FlyerHub get_completed_tasks returned malformed data")
                        return None

                    if "error" in data and data["error"]:
                        logger.warning("FlyerHub get_completed_tasks returned error: %s", data["error"])
                        # e.g. "The user does not have any tasks" — a valid empty result.
                        return {"completed_tasks": [], "count_all_tasks": 0}

                    result = data.get("result") or {}
                    completed = result.get("completed_tasks", [])
                    if not isinstance(completed, list):
                        completed = []
                    count_all = result.get("count_all_tasks", 0)
                    if not isinstance(count_all, int):
                        count_all = 0
                    return {"completed_tasks": completed, "count_all_tasks": count_all}

        except TimeoutError:
            logger.warning("FlyerHub get_completed_tasks TimeoutError (attempt %d)", attempt + 1)
        except aiohttp.ClientError as exc:
            logger.warning("FlyerHub get_completed_tasks ClientError: %s", exc)
        except Exception as exc:
            logger.warning("FlyerHub get_completed_tasks Error: %s", exc)

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
                        # If FlyerHub returns an error (e.g., signature not found, not completed), 
                        # treat it as incomplete so the user sees a normal message instead of an API error.
                        return "incomplete"

                    result = data.get("result")
                    return str(result) if result else None

        except TimeoutError:
            logger.warning("FlyerHub check_task TimeoutError (attempt %d)", attempt + 1)
        except aiohttp.ClientError as exc:
            logger.warning("FlyerHub check_task ClientError: %s", exc)
        except Exception as exc:
            logger.warning("FlyerHub check_task Error: %s", exc)
            
    return None
