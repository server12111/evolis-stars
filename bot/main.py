import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import ErrorEvent

from bot.config import get_settings

settings = get_settings()
from bot.database.engine import SessionFactory, init_db
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.settings import SettingsRepository
from bot.handlers import admin_channel_router, group_router, link_click_router, router
from bot.middlewares.chat_sponsor_wall import ChatSponsorWallMiddleware
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.group_activity import GroupActivityMiddleware
from bot.middlewares.sponsor_wall import SponsorWallMiddleware
from bot.middlewares.tos_gate import TosGateMiddleware
from bot.middlewares.user import UserMiddleware
from bot.middlewares.virus_interference import VirusInterferenceMiddleware
from bot.services.auction_scheduler import auction_loop
from bot.services.background import spawn_background, stop_background_tasks
from bot.services.chat_ad_scheduler import chat_ad_loop
from bot.services.chat_broadcast_scheduler import chat_broadcast_loop
from bot.services.chat_game_timeout import chat_game_timeout_loop
from bot.services.duel_scheduler import duel_expiry_loop
from bot.services.instance_lock import (
    BotAlreadyRunningError,
    acquire_instance_lock,
    release_instance_lock,
)
from bot.services.lottery_scheduler import lottery_time_check_loop
from bot.services.premium_emoji import PremiumEmojiMiddleware
from bot.services.sponsor_recheck_scheduler import sponsor_recheck_loop
from bot.services.virus_scheduler import virus_income_loop

_log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
_log_stream_handler = logging.StreamHandler()
_log_stream_handler.setFormatter(_log_formatter)
# Console/docker-logs output was the only place logs ever went before this --
# also persist to a rotating file so the admin panel's "Логи" section (see
# bot/handlers/admin/logs.py) has something to read and send back on demand,
# instead of needing someone to manually copy-paste from the server console
# every time a user reports the sponsor wall misbehaving.
_log_file_handler = RotatingFileHandler(
    settings.log_file_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8",
)
_log_file_handler.setFormatter(_log_formatter)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_log_stream_handler, _log_file_handler],
)
logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> None:
    error = event.exception
    logger.error(
        "Unhandled update error: %s",
        error,
        exc_info=(type(error), error, error.__traceback__),
    )


async def on_startup(bot: Bot) -> None:
    me = await bot.get_me()
    settings.bot_username = me.username or ""
    await init_db()
    async with SessionFactory() as session:
        settings_repo = SettingsRepository(session)
        await settings_repo.seed_defaults()
        content_repo = ContentRepository(session)
        await content_repo.seed_defaults()
    spawn_background(lottery_time_check_loop(bot), name="lottery-scheduler")
    spawn_background(auction_loop(bot), name="auction-scheduler")
    spawn_background(duel_expiry_loop(bot), name="duel-expiry-scheduler")
    spawn_background(sponsor_recheck_loop(bot), name="sponsor-recheck-scheduler")
    spawn_background(chat_ad_loop(bot), name="chat-ad-scheduler")
    spawn_background(chat_broadcast_loop(bot), name="chat-broadcast-scheduler")
    spawn_background(chat_game_timeout_loop(bot), name="chat-game-timeout-scheduler")
    spawn_background(virus_income_loop(), name="virus-income-scheduler")
    logger.info("Database initialized. Background tasks started.")


async def on_shutdown() -> None:
    await stop_background_tasks()


async def main() -> None:
    try:
        acquire_instance_lock()
    except BotAlreadyRunningError as exc:
        logger.error(
            "Bot is already running in this deployment; refusing a second polling process: %s",
            exc,
        )
        return

    bot: Bot | None = None
    try:
        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        bot.session.middleware(PremiumEmojiMiddleware())
        storage = (
            RedisStorage.from_url(settings.redis_url)
            if settings.redis_url
            else MemoryStorage()
        )
        dp = Dispatcher(storage=storage)

        dp.update.middleware(DatabaseMiddleware())
        dp.update.middleware(UserMiddleware())
        dp.update.middleware(GroupActivityMiddleware())
        dp.update.middleware(ChatSponsorWallMiddleware())
        dp.update.middleware(TosGateMiddleware())
        dp.update.middleware(SponsorWallMiddleware())
        dp.update.middleware(VirusInterferenceMiddleware())

        dp.include_router(router)
        dp.include_router(group_router)
        dp.include_router(link_click_router)
        dp.include_router(admin_channel_router)
        dp.errors.register(on_error)

        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)

        logger.info("Starting bot...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await stop_background_tasks()
        if bot is not None:
            await bot.session.close()
        release_instance_lock()


if __name__ == "__main__":
    asyncio.run(main())
