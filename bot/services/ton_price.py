import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price"
_RETRY_DELAYS = [2, 4]


async def get_ton_usd_rate() -> float | None:
    """Current TON price in USD, or None if the rate genuinely can't be
    fetched right now -- callers must never guess/fall back to a stale or
    made-up rate for a real payout, so None always means "block the flow
    and ask the user to retry later", same convention as every other
    provider integration (check_tgrass/check_botohub/etc.) in this repo."""
    params = {"ids": "the-open-network", "vs_currencies": "usd"}
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
                async with client.get(COINGECKO_API, params=params) as resp:
                    if resp.status == 429:
                        logger.warning("CoinGecko HTTP 429 (attempt %d), retrying...", attempt + 1)
                        continue
                    if resp.status >= 400:
                        logger.warning("CoinGecko returned HTTP %s", resp.status)
                        return None
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        logger.warning("CoinGecko returned malformed data")
                        return None
                    ton_data = data.get("the-open-network")
                    rate = ton_data.get("usd") if isinstance(ton_data, dict) else None
                    if not isinstance(rate, (int, float)) or rate <= 0:
                        logger.warning("CoinGecko returned invalid TON rate: %r", rate)
                        return None
                    return float(rate)
        except Exception as e:
            # Retry (not an immediate give-up) -- a one-off network blip
            # must not read as "CoinGecko is down" for the whole attempt.
            logger.warning("CoinGecko TON rate fetch error (attempt %d): %s", attempt + 1, e)
    return None
