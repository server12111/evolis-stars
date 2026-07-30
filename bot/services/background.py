import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task[Any]] = set()


def spawn_background(
    coroutine: Coroutine[Any, Any, Any],
    *,
    name: str,
) -> asyncio.Task[Any]:
    """Keep a strong task reference and log unexpected task failures."""
    task = asyncio.create_task(coroutine, name=name)
    _tasks.add(task)

    def _done(completed: asyncio.Task[Any]) -> None:
        _tasks.discard(completed)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            logger.error(
                "Background task %s failed",
                completed.get_name(),
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(_done)
    return task


async def stop_background_tasks() -> None:
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
