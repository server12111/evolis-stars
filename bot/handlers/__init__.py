from aiogram import F, Router

from bot.handlers import (
    start, earn, withdraw, bonus, tasks,
    profile, top, games, duel, cases, wheel, lottery,
    casino, mines, tower, auction, random_game, mychats, exchange,
)
from bot.handlers.admin import router as admin_router
from bot.handlers.admin_channel import router as admin_channel_router  # noqa: F401 — re-exported; chat-type agnostic, included directly in main.py
from bot.handlers.group import router as group_router  # noqa: F401 — re-exported for main.py
from bot.handlers.link_click import router as link_click_router  # noqa: F401 — re-exported; chat-type agnostic, included directly in main.py

router = Router()

# Every handler included below this point assumes a private 1:1 DM (they
# read/write a single db_user with no chat-scoping) — this filter keeps
# group-chat updates from ever reaching them. Filtering here, on the root
# router, is sufficient for every included sub_router: aiogram's Router
# checks the parent's root filters before delegating to any sub_router.
router.message.filter(F.chat.type == "private")
router.callback_query.filter(lambda c: c.message is not None and c.message.chat.type == "private")

router.include_router(start.router)
router.include_router(earn.router)
router.include_router(withdraw.router)
router.include_router(bonus.router)
router.include_router(tasks.router)
router.include_router(profile.router)
router.include_router(top.router)
router.include_router(games.router)
router.include_router(casino.router)
router.include_router(duel.router)
router.include_router(cases.router)
router.include_router(wheel.router)
router.include_router(lottery.router)
router.include_router(mines.router)
router.include_router(tower.router)
router.include_router(auction.router)
router.include_router(random_game.router)
router.include_router(mychats.router)
router.include_router(exchange.router)
router.include_router(admin_router)
