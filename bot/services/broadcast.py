import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


async def _send_content(
    bot: Bot,
    chat_id: int,
    message: Message,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    """Re-sends via bot.send_* (not copy_message) so the text/caption flows
    through PremiumEmojiMiddleware and gets the same premium-emoji
    decoration as every other bot-authored message — copy_message has no
    text/caption field for the middleware to rewrite."""
    caption = message.caption_html if message.caption else None
    if message.photo:
        await bot.send_photo(chat_id, message.photo[-1].file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    elif message.video:
        await bot.send_video(chat_id, message.video.file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    elif message.document:
        await bot.send_document(chat_id, message.document.file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    elif message.animation:
        await bot.send_animation(chat_id, message.animation.file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    elif message.text:
        await bot.send_message(chat_id, message.html_text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        # Exotic content (voice, sticker, ...) — no text/caption to decorate
        # anyway, fall back to a plain copy so it still sends.
        await message.copy_to(chat_id, reply_markup=reply_markup)


async def broadcast(
    bot: Bot,
    target_ids: list[int],
    source_message: Message,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> tuple[int, int]:
    """Send source_message content to every id in target_ids — private user
    ids or group chat ids both work the same way. Returns (success, fail)."""
    success = 0
    fail = 0
    for target_id in target_ids:
        try:
            await _send_content(bot, target_id, source_message, reply_markup)
            success += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)  # ~20 messages/sec
    return success, fail
