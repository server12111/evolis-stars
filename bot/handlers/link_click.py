import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.link_clicks import LinkButtonRepository, LinkClickRepository
from bot.services.chat_revenue import settle_chat_ad_revenue

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("lc:"))
async def cb_link_click(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    try:
        link_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer()
        return

    button = await LinkButtonRepository(session).get(link_id)
    if not button or not button.is_active:
        logger.warning(
            "LINK_CLICK inactive link_id=%s found=%s is_active=%s uid=%s",
            link_id, button is not None, getattr(button, "is_active", None), callback.from_user.id,
        )
        await callback.answer("⚠️ Ссылка больше не активна.", show_alert=True)
        return

    chat_id = callback.message.chat.id if callback.message.chat.type in ("group", "supergroup") else None
    click_repo = LinkClickRepository(session)
    is_first_click = await click_repo.record_click(link_id, callback.from_user.id, chat_id)

    if is_first_click and chat_id is not None:
        chat = await ChatRepository(session).get(chat_id)
        if chat:
            await settle_chat_ad_revenue(session, chat)

    # answerCallbackQuery's url can only reopen the bot itself, never an
    # arbitrary external link — start.py's lc_ branch delivers the real
    # destination as a normal message instead.
    await callback.answer(url=f"https://t.me/{settings.bot_username}?start=lc_{link_id}")
