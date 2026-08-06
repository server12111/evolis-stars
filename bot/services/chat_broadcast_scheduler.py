import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from bot.database.engine import SessionFactory
from bot.database.models import Chat, ChatBroadcastMessage
from bot.database.repositories.chat_broadcast import (
    ChatBroadcastRepository,
    load_buttons,
    load_photo_ids,
)

logger = logging.getLogger(__name__)

# Owner-set intervals can be as short as a few minutes, far finer-grained
# than the existing 15-min BotoHub-views pass, so this loop checks more
# often.
_PASS_INTERVAL_SECONDS = 30


async def chat_broadcast_loop(bot: Bot) -> None:
    while True:
        try:
            await _run_pass(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Chat broadcast scheduler error")
        await asyncio.sleep(_PASS_INTERVAL_SECONDS)


async def _run_pass(bot: Bot) -> None:
    async with SessionFactory() as session:
        repo = ChatBroadcastRepository(session)
        now = datetime.utcnow()
        for chat in await repo.due_chats(now):
            await _send_one(bot, session, chat, now)


def _buttons_markup(buttons: list[dict]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b["text"], url=b["url"])] for b in buttons
    ])


async def _dispatch(bot: Bot, chat_id: int, message: ChatBroadcastMessage) -> None:
    photos = load_photo_ids(message)
    kb = _buttons_markup(load_buttons(message))

    if not photos:
        await bot.send_message(chat_id, message.text, reply_markup=kb)
        return

    if len(photos) == 1:
        await bot.send_photo(chat_id, photos[0], caption=message.text, reply_markup=kb)
        return

    # sendMediaGroup doesn't support reply_markup at all — if there are
    # buttons too, they go out as a short separate follow-up message right
    # after the album, since Telegram gives no way to attach them to the
    # album itself.
    media = [
        InputMediaPhoto(media=file_id, caption=message.text if i == 0 else None)
        for i, file_id in enumerate(photos)
    ]
    await bot.send_media_group(chat_id, media)
    if kb:
        try:
            await bot.send_message(chat_id, "🔗", reply_markup=kb)
        except Exception as exc:
            # The album itself already went out successfully — a failure
            # here must not make _send_one treat the whole round as failed
            # and resend the entire album next pass over a missing button.
            logger.warning("Custom broadcast: album sent but buttons follow-up failed in chat %s: %s", chat_id, exc)


async def _send_one(bot: Bot, session, chat: Chat, now: datetime) -> None:
    repo = ChatBroadcastRepository(session)
    messages = await repo.list_approved(chat.chat_id)
    if not messages:
        # Only disable if there's truly nothing left (everything deleted
        # or rejected) — a text still awaiting moderation must NOT disable
        # the chat, it just has nothing to send yet this pass.
        if await repo.count(chat.chat_id) == 0:
            chat.custom_broadcast_enabled = False
            await session.commit()
        return

    index = chat.custom_broadcast_next_index % len(messages)
    try:
        await _dispatch(bot, chat.chat_id, messages[index])
    except Exception as exc:
        logger.warning("Cannot send custom broadcast into chat %s: %s", chat.chat_id, exc)
        return

    chat.custom_broadcast_last_sent_at = now
    chat.custom_broadcast_next_index = (index + 1) % len(messages)
    await session.commit()
