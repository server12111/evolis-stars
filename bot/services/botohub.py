import logging
import aiohttp

logger = logging.getLogger(__name__)

BOTOHUB_API = "https://botohub.me/get-tasks"


async def check_botohub(user_id: int, api_key: str) -> list[dict] | None:
    """Return unsubscribed channels; return None when the integration is unavailable.

    Docs: https://botohub.me/integration  (вкладка «ОП (/get-tasks)»)
    Endpoint: POST /get-tasks
    Response fields:
      - tasks: string[] — массив ссылок на спонсоров
      - completed: bool — все спонсоры выполнены (return empty list)
      - skip: bool      — нет спонсоров для показа (return empty list)
    Notes:
      - BotoHub возвращает только URL без названий каналов.
        Имя канала извлекается из URL (@username), иначе «Канал».
      - Спонсоры закрепляются за пользователем на 3 минуты.
    """
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

                # completed=true или skip=true — всё выполнено / нет заданий
                if data.get("completed") or data.get("skip"):
                    return []

                tasks = data.get("tasks", [])
                result = []
                for url in tasks:
                    if not url:
                        continue
                    result.append({"name": "Канал", "url": url})
                return result
    except Exception as e:
        logger.warning("BotoHub check error: %s", e)
    return None


