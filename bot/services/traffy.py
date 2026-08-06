import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

TRAFFY_API = "https://traffy.ai/publisher"
_RETRY_DELAYS = [2, 4]

# Per https://traffy.ai/publisher/docs#result-codes — /tasks/check result.status.
COMPLETED_STATUSES = {"completed"}
# Statuses Traffy will never move to "completed" from -- confirmed live: a
# user's traffy count stayed stuck at 4 for 13+ minutes because "rejected"
# items were neither "completed" nor absent from the response, so the old
# dict[str, bool] contract (True only for "completed") had no way for a
# caller to tell a terminal rejection apart from a genuinely still-pending
# item. Same class of bug as FlyerHub's "unavailable", same fix: treated as
# resolved so the wall stops blocking the user on it.
TERMINAL_STATUSES = COMPLETED_STATUSES | {"rejected"}


def _headers(api_key: str) -> dict:
    return {"x-publisher-api-key": api_key, "Content-Type": "application/json"}


async def get_traffy_tasks(
    api_key: str,
    user_id: int,
    limit: int = 10,
    *,
    first_name: str | None = None,
    username: str | None = None,
    language_code: str | None = None,
) -> list[dict] | None:
    """Fetch a fresh batch of advertisers ("full API" mode).

    Docs: https://traffy.ai/publisher/docs — POST /publisher/tasks
    Returns decorated sponsor items (url/name/ref), or None when the
    integration is unavailable. Each call hands out NEW assignment_id
    values — callers must not call this again for an already-frozen wave;
    re-check saved assignment_ids via check_traffy_tasks() instead.
    """
    if not api_key:
        return []
    body: dict = {"telegram_id": user_id, "limit": max(1, min(10, limit))}
    if first_name:
        body["first_name"] = first_name
    if username:
        body["username"] = username
    if language_code:
        body["language_code"] = language_code

    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
                async with client.post(
                    f"{TRAFFY_API}/tasks",
                    json=body,
                    headers=_headers(api_key),
                ) as resp:
                    if resp.status == 429:
                        logger.warning("Traffy get_tasks HTTP 429 (attempt %d), retrying...", attempt + 1)
                        continue
                    if resp.status == 401:
                        # Bad key, API disabled, or bot not yet moderated/active.
                        logger.warning("Traffy get_tasks HTTP 401 (key/moderation)")
                        return None
                    if resp.status >= 400:
                        logger.warning("Traffy get_tasks HTTP %s", resp.status)
                        return None
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict) or not data.get("ok"):
                        logger.warning("Traffy get_tasks returned malformed/failed data")
                        return None
                    tasks = data.get("tasks", [])
                    if not isinstance(tasks, list):
                        logger.warning("Traffy get_tasks returned invalid tasks")
                        return None
                    result = []
                    for task in tasks:
                        if not isinstance(task, dict):
                            continue
                        url = str(task.get("target_link", ""))
                        assignment_id = str(task.get("assignment_id", ""))
                        if not url or not assignment_id:
                            continue
                        result.append({
                            "name": str(task.get("title") or "Спонсор"),
                            "url": url,
                            "ref": assignment_id,
                        })
                    logger.info("Traffy get_tasks user_id=%d → %d tasks", user_id, len(result))
                    return result
        except Exception as e:
            # Retry (not an immediate give-up) -- a one-off network blip
            # must not read as "this provider is down" and cascade into
            # an "unavailable" wall for anyone checking in that exact
            # moment, same as the HTTP 429 branch above already does.
            logger.warning("Traffy get_tasks error (attempt %d): %s", attempt + 1, e)
    return None


async def check_traffy_tasks(
    api_key: str,
    user_id: int,
    assignment_ids: list[str],
) -> dict[str, str] | None:
    """Check status for previously-issued assignment_ids.

    Docs: POST /publisher/tasks/check
    Returns {assignment_id: raw status string} for every id Traffy's
    response actually mentions, or None when the integration is
    unavailable. An id Traffy no longer recognizes (expired/not_found/
    telegram_id_mismatch) is simply omitted -- callers must treat a
    missing key as resolved (see TERMINAL_STATUSES), not as still
    pending, or the wall would block the user on it forever.
    """
    if not api_key or not assignment_ids:
        return {}
    body = {"telegram_id": user_id, "assignment_ids": assignment_ids}

    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
                async with client.post(
                    f"{TRAFFY_API}/tasks/check",
                    json=body,
                    headers=_headers(api_key),
                ) as resp:
                    if resp.status == 429:
                        logger.warning("Traffy check_tasks HTTP 429 (attempt %d), retrying...", attempt + 1)
                        continue
                    if resp.status >= 400:
                        logger.warning("Traffy check_tasks HTTP %s", resp.status)
                        return None
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict) or not data.get("ok"):
                        logger.warning("Traffy check_tasks returned malformed/failed data")
                        return None
                    results = data.get("results", [])
                    if not isinstance(results, list):
                        logger.warning("Traffy check_tasks returned invalid results")
                        return None
                    statuses: dict[str, str] = {}
                    for item in results:
                        if not isinstance(item, dict):
                            continue
                        assignment_id = str(item.get("assignment_id", ""))
                        if not assignment_id:
                            continue
                        statuses[assignment_id] = str(item.get("status", ""))
                    missing = [aid for aid in assignment_ids if aid not in statuses]
                    logger.info(
                        "Traffy check_tasks raw statuses: %s; missing from response: %s",
                        statuses, missing,
                    )
                    return statuses
        except Exception as e:
            # Retry -- same reasoning as get_traffy_tasks above.
            logger.warning("Traffy check_tasks error (attempt %d): %s", attempt + 1, e)
    return None
