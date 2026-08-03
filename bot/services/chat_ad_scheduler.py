import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from bot.config import get_settings
from bot.database.engine import SessionFactory
from bot.database.models import Chat, ChatLinkButton, ChatMembership, User
from bot.database.repositories.chat_ads import ChatAdRepository
from bot.database.repositories.link_clicks import LinkButtonRepository
from bot.services.adv import send_ad
from bot.services.chat_revenue import settle_chat_ad_revenue

logger = logging.getLogger(__name__)
settings = get_settings()

_VIEWS_PASS_INTERVAL_SECONDS = 900  # 15 min
_AD_SEND_COOLDOWN = timedelta(hours=1)
_CLICK_POST_INTERVAL = timedelta(hours=3)
_last_click_post_at: dict[int, datetime] = {}


async def chat_ad_loop(bot: Bot) -> None:
    """Two independent jobs for every chat with broadcast_opt_in, run every
    pass: (1) DM a BotoHub ad to registered members not messaged recently
    (the "views" proxy, since BotoHub has no group-chat concept at all —
    see chat_eligibility docs), (2) occasionally post one in-chat message
    with a click-tracked link button (the "clicks" half — a fully in-house
    mechanism, since BotoHub can't serve into chats either)."""
    while True:
        try:
            await _run_pass(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Chat ad scheduler error")
        await asyncio.sleep(_VIEWS_PASS_INTERVAL_SECONDS)


async def _run_pass(bot: Bot) -> None:
    async with SessionFactory() as session:
        result = await session.execute(
            select(Chat).where(Chat.broadcast_opt_in == True, Chat.status == "active")  # noqa: E712
        )
        chats = list(result.scalars().all())

        for chat in chats:
            # Views (BotoHub DM) and clicks (in-house in-chat post) are
            # independent mechanisms — a missing/misconfigured BotoHub key
            # must not also block the in-house click ads from posting.
            if settings.botohub_views_key:
                await _send_views_for_chat(session, chat)
            await _maybe_post_click_ad(bot, session, chat)
            await settle_chat_ad_revenue(session, chat)


async def _send_views_for_chat(session, chat: Chat) -> None:
    members_result = await session.execute(
        select(ChatMembership.user_id)
        .join(User, User.user_id == ChatMembership.user_id)
        .where(ChatMembership.chat_id == chat.chat_id, ChatMembership.left_at.is_(None))
    )
    member_ids = [row[0] for row in members_result.all()]
    if not member_ids:
        return

    ad_repo = ChatAdRepository(session)
    now = datetime.utcnow()
    for user_id in member_ids:
        last_sent = await ad_repo.last_sent_at(user_id)
        if last_sent and now - last_sent < _AD_SEND_COOLDOWN:
            continue
        delivered = await send_ad(settings.botohub_views_key, user_id, hi=False)
        if delivered:
            await ad_repo.record_send(chat.chat_id, user_id)


async def _maybe_post_click_ad(bot: Bot, session, chat: Chat) -> None:
    last_posted = _last_click_post_at.get(chat.chat_id)
    now = datetime.utcnow()
    if last_posted and now - last_posted < _CLICK_POST_INTERVAL:
        return

    link_repo = LinkButtonRepository(session)
    buttons = await link_repo.all_active()
    if not buttons:
        return

    button: ChatLinkButton = buttons[0]
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=button.label, callback_data=f"lc:{button.id}"))

    try:
        await bot.send_message(
            chat.chat_id,
            f"📣 <b>{button.label}</b>",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        _last_click_post_at[chat.chat_id] = now
    except Exception as exc:
        logger.warning("Cannot post click-ad into chat %s: %s", chat.chat_id, exc)
