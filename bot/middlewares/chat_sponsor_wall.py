import logging
import time
from html import escape
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Update

from bot.config import get_settings
from bot.database.models import Chat
from bot.database.repositories.chat_sponsor_wall import ChatSponsorWallRepository

settings = get_settings()
logger = logging.getLogger(__name__)

# Collapses a rapid-fire double message from the same still-blocked member
# into one wall message instead of spamming a second one moments later --
# the message is still deleted every single time regardless of this, only
# the "here's who to subscribe to" reminder is debounced.
_WALL_MESSAGE_COOLDOWN_SECONDS = 8
_last_wall_shown: dict[tuple[int, int], float] = {}
# Every (chat_id, user_id) pair that ever gets blocked adds a permanent
# entry here otherwise -- opportunistically sweep out anything well past
# its cooldown once the dict gets large, instead of a dedicated background
# task, keeping the common case (small dict) a plain no-op check.
_PRUNE_THRESHOLD = 5000
_PRUNE_MAX_AGE_SECONDS = _WALL_MESSAGE_COOLDOWN_SECONDS * 450  # ~1 hour


def _prune_last_wall_shown(now: float) -> None:
    if len(_last_wall_shown) < _PRUNE_THRESHOLD:
        return
    stale = [key for key, ts in _last_wall_shown.items() if now - ts > _PRUNE_MAX_AGE_SECONDS]
    for key in stale:
        del _last_wall_shown[key]


class ChatSponsorWallMiddleware(BaseMiddleware):
    """Deletes a group member's message until they've subscribed to every
    active sponsor their chat owner attached to the chat's mandatory
    sponsor wall (bot/handlers/group/chat_sponsor_wall.py /
    bot/database/repositories/chat_sponsor_wall.py). Registered right
    after GroupActivityMiddleware in bot/main.py -- needs its
    data["chat"] -- and before TosGateMiddleware/SponsorWallMiddleware."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update) or event.message is None:
            return await handler(event, data)

        message = event.message
        event_chat = data.get("event_chat")
        if event_chat is None or event_chat.type not in ("group", "supergroup"):
            return await handler(event, data)

        chat: Chat | None = data.get("chat")
        session = data.get("session")
        bot: Bot | None = data.get("bot")
        user = data.get("event_from_user")
        if chat is None or session is None or bot is None or user is None or user.is_bot:
            return await handler(event, data)

        # A real Telegram Stars payment confirmation must always reach its
        # handler -- same money-safety carve-out SponsorWallMiddleware/
        # VirusInterferenceMiddleware already make. Service messages (join/
        # leave notices etc.) are left alone too -- deleting those would be
        # bizarre and they're not something a member "wrote".
        if (
            message.successful_payment is not None
            or message.new_chat_members
            or message.left_chat_member is not None
        ):
            return await handler(event, data)

        wall_repo = ChatSponsorWallRepository(session)
        active = await wall_repo.list_active(chat.chat_id)
        if not active:
            # Cheap early-out for the overwhelming common case (no wall
            # configured for this chat at all) -- one count-free list
            # query, same per-message cost model GroupActivityMiddleware
            # already established for this exact chat.
            return await handler(event, data)

        if chat.owner_user_id == user.id or user.id in settings.admin_id_list:
            return await handler(event, data)

        completed_ids = await wall_repo.completed_sponsor_ids([s.id for s in active], user.id)
        if len(completed_ids) >= len(active):
            # Common case for anyone who already passed the wall once --
            # no Telegram API call needed at all.
            return await handler(event, data)

        # Not yet complete -- only NOW is a live API call worth spending,
        # to check whether this specific not-yet-verified member happens
        # to be a Telegram chat-admin (exempt), bounded to actual
        # unsubscribed traffic rather than every single message.
        try:
            member = await bot.get_chat_member(chat.chat_id, user.id)
            if member.status in ("administrator", "creator"):
                return await handler(event, data)
        except Exception:
            pass

        try:
            await bot.delete_message(chat.chat_id, message.message_id)
        except Exception as exc:
            logger.debug("Cannot delete message %s in chat %s: %s", message.message_id, chat.chat_id, exc)

        key = (chat.chat_id, user.id)
        now = time.time()
        if now - _last_wall_shown.get(key, 0.0) >= _WALL_MESSAGE_COOLDOWN_SECONDS:
            _prune_last_wall_shown(now)
            _last_wall_shown[key] = now
            from bot.handlers.group.chat_sponsor_wall import wall_subscribe_kb

            missing = [s for s in active if s.id not in completed_ids]
            display_name = escape(user.first_name or user.username or "участник")
            mention = f'<a href="tg://user?id={user.id}">{display_name}</a>'
            try:
                await bot.send_message(
                    chat.chat_id,
                    f"🚧 {mention}, чтобы писать в этом чате, подпишись на спонсоров ниже и нажми «Проверить»:",
                    parse_mode="HTML",
                    reply_markup=wall_subscribe_kb(missing, chat.chat_id),
                )
            except Exception as exc:
                logger.debug("Cannot send wall message to chat %s: %s", chat.chat_id, exc)

        # Swallow the update -- the message no longer exists in the chat,
        # so no downstream handler (game commands etc.) should ever act on
        # its content.
        return None
