from aiogram import Router

from bot.handlers.admin import (
    blocked_sponsors,
    chats,
    stats,
    broadcast,
    users,
    promo,
    tasks,
    games,
    settings,
    media,
    own_sponsors,
)
from bot.handlers.admin import lottery as admin_lottery

router = Router()
router.include_router(stats.router)
router.include_router(broadcast.router)
router.include_router(users.router)
router.include_router(promo.router)
router.include_router(tasks.router)
router.include_router(games.router)
router.include_router(settings.router)
router.include_router(media.router)
router.include_router(admin_lottery.router)
router.include_router(own_sponsors.router)
router.include_router(blocked_sponsors.router)
router.include_router(chats.router)
