import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
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

# Substrings of a TelegramBadRequest's message that mean the CONTENT itself
# is unsendable (bad URL, unparseable entities, etc.) and will fail on
# every future pass too -- as opposed to e.g. CHAT_WRITE_FORBIDDEN/"not
# enough rights", which is about the chat's current permission state and
# can resolve itself. Deliberately an allowlist (fail closed, i.e. default
# to NOT deleting content) rather than trying to enumerate every transient
# error Telegram might return.
_PERMANENT_CONTENT_ERROR_MARKERS = (
    "wrong http url",
    "button_url_invalid",
    "can't parse entities",
    "message is too long",
    "message_too_long",
    "text is empty",
)


def _is_permanent_content_error(exc: TelegramBadRequest) -> bool:
    error_text = str(exc).lower()
    return any(marker in error_text for marker in _PERMANENT_CONTENT_ERROR_MARKERS)


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

    # message.text_is_html distinguishes the two shapes text can be in:
    # - True: aiogram's html_text serialization (mychats.py's owner-text
    #   capture) — real formatting/premium-emoji entities are valid <tag>s
    #   and any literal "<"/">"/"&" the owner typed as plain characters
    #   were already escaped, so parse_mode="HTML" renders the premium
    #   emoji as intended. mychats.py separately rejects text_link
    #   entities with an unsafe url before they ever reach this table (see
    #   its own comment) — aiogram's html_text does NOT escape a link's
    #   own url when splicing it into href="...".
    # - False (rows from before this existed): raw, unescaped free text.
    #   The bot has a global parse_mode=HTML default, so leaving parse_mode
    #   unset here would make Telegram try to parse it as HTML: an
    #   entirely ordinary message containing a bare "<", ">" or "&" (e.g.
    #   "цена < 100⭐") raises "can't parse entities" and permanently fails
    #   to send. This text was never meant to be formatted, so parse_mode
    #   is explicitly off.
    parse_mode = "HTML" if message.text_is_html else None
    if not photos:
        await bot.send_message(chat_id, message.text, parse_mode=parse_mode, reply_markup=kb)
        return

    if len(photos) == 1:
        await bot.send_photo(chat_id, photos[0], caption=message.text, parse_mode=parse_mode, reply_markup=kb)
        return

    # sendMediaGroup doesn't support reply_markup at all — if there are
    # buttons too, they go out as a short separate follow-up message right
    # after the album, since Telegram gives no way to attach them to the
    # album itself.
    media = [
        InputMediaPhoto(media=file_id, caption=message.text if i == 0 else None, parse_mode=parse_mode)
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
    message = messages[index]
    try:
        await _dispatch(bot, chat.chat_id, message)
    except TelegramBadRequest as exc:
        if not _is_permanent_content_error(exc):
            # TelegramBadRequest also covers things like CHAT_WRITE_FORBIDDEN
            # /"not enough rights" -- about the CHAT's current permissions,
            # not this message, and can resolve itself (slow mode lifted,
            # admin restores posting rights). Deleting the owner's content
            # over a transient state like that would be a permanent loss
            # for a temporary condition -- log and retry next pass instead,
            # same as any other non-fatal error.
            logger.warning("Cannot send custom broadcast into chat %s: %s", chat.chat_id, exc)
            return
        # A malformed button URL, unparseable entities, etc. -- Telegram
        # will reject this exact same message on every future pass too, so
        # retrying it forever (the old behaviour) would spam an identical
        # warning every 30s and starve every other message in this chat's
        # rotation. Drop it and tell the owner why, same as a moderator
        # rejecting it, instead of looping on it permanently.
        logger.warning(
            "Custom broadcast permanently rejected by Telegram, dropping message %s in chat %s: %s",
            message.id, chat.chat_id, exc,
        )
        await repo.delete_message(chat.chat_id, message.id)
        if chat.owner_user_id:
            try:
                await bot.send_message(
                    chat.owner_user_id,
                    f"❌ Рассылка в чате не отправилась и была удалена: {exc}\n\n"
                    f"Проверьте текст/кнопки (ссылки должны вести на настоящий сайт или t.me) и отправьте заново.",
                )
            except Exception:
                pass
        return
    except Exception as exc:
        logger.warning("Cannot send custom broadcast into chat %s: %s", chat.chat_id, exc)
        return

    chat.custom_broadcast_last_sent_at = now
    chat.custom_broadcast_next_index = (index + 1) % len(messages)
    await session.commit()
