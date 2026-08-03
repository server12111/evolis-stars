from aiogram import F, Router

from bot.handlers.group import (
    chat_bonus,
    chat_leaderboard,
    chat_promo,
    games_doors,
    games_maze,
    games_roulette,
    games_safe,
    link_click,
    membership,
    onboarding,
    owner_menu,
)

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))
router.callback_query.filter(
    lambda c: c.message is not None and c.message.chat.type in ("group", "supergroup")
)

# my_chat_member / chat_member observers are inherently chat-scoped already
# (they always carry a .chat) — no message/callback_query filter applies to
# those event types, so onboarding/membership work unfiltered here.
router.include_router(onboarding.router)
router.include_router(membership.router)
router.include_router(owner_menu.router)
router.include_router(chat_promo.router)
router.include_router(chat_bonus.router)
router.include_router(link_click.router)
router.include_router(games_roulette.router)
router.include_router(games_safe.router)
router.include_router(games_maze.router)
router.include_router(games_doors.router)
router.include_router(chat_leaderboard.router)
