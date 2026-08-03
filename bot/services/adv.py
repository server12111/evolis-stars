import logging

import aiohttp

logger = logging.getLogger(__name__)

_ADV_URL = "https://views.botohub.me/ad/SendPost"

# Per https://views.botohub.me/integration — SendPostResult code meanings.
_RESULT_MEANINGS = {
    1: "Success",
    2: "RevokedTokenError — токен отозван/недействителен",
    3: "UserForbiddenError — пользователь заблокировал бота",
    4: "TooManyRequestsError — превышен лимит запросов",
    5: "OtherBotApiError — другая ошибка Telegram API",
    6: "OtherError — внутренняя ошибка BotoHub",
    7: "AdLimited — достигнут лимит показов",
    8: "NoAds — нет доступных объявлений",
    9: "BotIsNotEnabled — бот/режим отключен в настройках BotoHub",
    10: "Banned — бот заблокирован в BotoHub Views",
    11: "InReview — бот на модерации BotoHub",
}


async def send_ad(api_key: str, user_id: int, hi: bool = False) -> None:
    """Fire-and-forget ad delivery. hi=True only after /start for new users."""
    if not api_key:
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
            async with client.post(
                _ADV_URL,
                json={"SendToChatId": user_id, "hi": hi},
                headers={"Authorization": api_key, "Content-Type": "application/json"},
            ) as resp:
                if resp.status >= 400:
                    logger.warning("BotoHub ad send HTTP %s for user %s", resp.status, user_id)
                    return
                data = await resp.json(content_type=None)
                code = data.get("SendPostResult", 0)
                if code not in (1, 7, 8):
                    logger.warning(
                        "Ad result for user %s: code=%s (%s)",
                        user_id, code, _RESULT_MEANINGS.get(code, "неизвестный код"),
                    )
    except Exception as e:
        logger.warning("Ad send error for user %s: %s", user_id, e)
