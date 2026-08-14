import logging
import time
from html import escape
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Update

from bot.config import get_settings
from bot.database.models import Chat, User
from bot.database.repositories.chat_sponsor_wall import ChatSponsorWallRepository
from bot.services.chat_wall_integrations import (
    WALL_INTEGRATION_FIELDS,
    safe_evaluate_and_credit_integration_wave,
    safe_pending_integration_items,
)

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
        integration_active = chat.wall_integration_enabled
        if not active and not integration_active:
            # Cheap early-out for the overwhelming common case (no wall
            # configured/enabled for this chat at all) -- one count-free
            # list query, same per-message cost model GroupActivityMiddleware
            # already established for this exact chat. Either the owner's
            # own sponsor list OR the paid-sponsors toggle alone is enough
            # to activate the wall -- an owner can run a paid-only wall
            # without ever adding one of their own sponsors.
            return await handler(event, data)

        if chat.owner_user_id == user.id or user.id in settings.admin_id_list:
            return await handler(event, data)

        completed_ids = await wall_repo.completed_sponsor_ids([s.id for s in active], user.id)
        owner_missing = [s for s in active if s.id not in completed_ids]

        # Integration (ad-network) offers are global per user, not per
        # chat -- read the User row (may not exist yet for a group member
        # who never started the bot privately; treat that as "nothing to
        # gate on" rather than blocking on missing data). The owner's own
        # toggle (chat.wall_integration_enabled) only controls whether THIS
        # chat requires/shows it -- it never touches the shared wave state,
        # so disabling it here doesn't affect any other walled chat where
        # it's still on.
        db_user = await session.get(User, user.id) if integration_active else None
        wave_field = getattr(db_user, WALL_INTEGRATION_FIELDS.wave) if db_user is not None else 3

        if not owner_missing and wave_field == 3:
            # Common case for anyone who already passed the wall once --
            # no Telegram/provider API call needed at all.
            return await handler(event, data)

        # Not yet complete -- only NOW is a live API call worth spending,
        # to check whether this specific not-yet-verified member happens
        # to be a Telegram chat-admin (exempt), bounded to actual
        # unsubscribed traffic rather than every single message. Checked
        # before the (potentially much more expensive) ad-network
        # provider round below, so an exempt admin never triggers one.
        try:
            member = await bot.get_chat_member(chat.chat_id, user.id)
            if member.status in ("administrator", "creator"):
                return await handler(event, data)
        except Exception:
            pass

        integration_missing: list[dict] = []
        integration_unavailable = False
        if db_user is not None:
            if wave_field == 0:
                # Never frozen for this user -- the one live provider round
                # a first-ever block is allowed to spend (see
                # safe_evaluate_and_credit_integration_wave's own docstring).
                wave_state, _ = await safe_evaluate_and_credit_integration_wave(message, db_user, session, bot)
                if wave_state.status == "pending":
                    integration_missing = wave_state.items or []
                elif wave_state.status == "unavailable":
                    # A provider outage (or an unexpected exception, safely
                    # degraded by the wrapper above) must still BLOCK (same
                    # as the /start wall's own _show_retry path) -- silently
                    # letting the message through here would bypass the
                    # paid wall entirely for as long as it lasts.
                    integration_unavailable = True
            elif wave_field != 3:
                # Already frozen -- DB-only, no provider call on this hot
                # path (the common case: a user's wave only equals 0 once,
                # ever, so this branch is hit far more often than the one
                # above).
                integration_missing, integration_unavailable = await safe_pending_integration_items(
                    db_user, session,
                )

        if not owner_missing and not integration_missing and not integration_unavailable:
            return await handler(event, data)

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

            display_name = escape(user.first_name or user.username or "участник")
            mention = f'<a href="tg://user?id={user.id}">{display_name}</a>'
            try:
                await bot.send_message(
                    chat.chat_id,
                    f"🚧 {mention}, чтобы писать в этом чате, подпишись на спонсоров ниже и нажми «Проверить»:",
                    parse_mode="HTML",
                    reply_markup=wall_subscribe_kb(owner_missing, chat.chat_id, integration_missing),
                )
            except Exception as exc:
                logger.debug("Cannot send wall message to chat %s: %s", chat.chat_id, exc)

        # Swallow the update -- the message no longer exists in the chat,
        # so no downstream handler (game commands etc.) should ever act on
        # its content.
        return None
