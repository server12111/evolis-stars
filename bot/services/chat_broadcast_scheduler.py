import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from aiogram import Bot

from bot.database.engine import SessionFactory
from bot.database.models import Chat
from bot.database.repositories.chat_broadcast import ChatBroadcastRepository
from bot.database.repositories.settings import SettingsRepository
from bot.services.chat_eligibility import credit_stars

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


async def _send_one(bot: Bot, session, chat: Chat, now: datetime) -> None:
    repo = ChatBroadcastRepository(session)
    messages = await repo.list_messages(chat.chat_id)
    if not messages:
        # Owner deleted every text after enabling — nothing left to rotate.
        chat.custom_broadcast_enabled = False
        await session.commit()
        return

    index = chat.custom_broadcast_next_index % len(messages)
    text = messages[index].text
    try:
        await bot.send_message(chat.chat_id, text)
    except Exception as exc:
        logger.warning("Cannot send custom broadcast into chat %s: %s", chat.chat_id, exc)
        return

    reward = Decimal(str(await SettingsRepository(session).get_float("broadcast_reward_per_send", 0.5)))
    if reward > 0:
        await credit_stars(session, chat.owner_user_id, reward)

    chat.custom_broadcast_last_sent_at = now
    chat.custom_broadcast_next_index = (index + 1) % len(messages)
    await session.commit()
