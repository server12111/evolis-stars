import logging

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.group.chat_bonus import try_link_pending_sponsor
from bot.keyboards.mychats import connected_instructions_kb

router = Router()
logger = logging.getLogger(__name__)
settings = get_settings()

_LEFT_STATUSES = {"left", "kicked"}
_PRESENT_STATUSES = {"member", "administrator", "restricted"}


async def _resolve_owner(bot: Bot, chat_id: int) -> int | None:
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except Exception as exc:
        logger.warning("Cannot resolve owner for chat %s: %s", chat_id, exc)
        return None
    for admin in admins:
        if admin.status == "creator":
            return admin.user.id
    return None


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    """Fires when the bot itself is added to / removed from / promoted in a
    chat. This is how a Chat row first gets created — no group.message
    handler can run before the bot has actually been added."""
    # A bonus owner adding this chat/channel specifically as a bonus
    # sponsor is a different flow entirely (no chat_min_members threshold,
    # no "only the owner may add" rule tied to *this* chat) — resolve that
    # first and skip normal chat-registration handling if it applies.
    if await try_link_pending_sponsor(bot, state.storage, event):
        return

    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status
    chat_id = event.chat.id

    if event.chat.type == "channel":
        # Every "chat owner" feature (promo codes, chat bonuses, games,
        # custom broadcast, top members) assumes member interaction a
        # channel fundamentally doesn't have — channels must never become
        # a manageable "Панель чатов" entry. mark_left here isn't just for
        # a genuine departure: it also self-heals a channel that was
        # incorrectly registered before this check existed, since
        # list_owned_by() excludes status="left" rows.
        await ChatRepository(session).mark_left(chat_id)
        return

    if new_status in _LEFT_STATUSES:
        await ChatRepository(session).mark_left(chat_id)
        return

    if new_status not in _PRESENT_STATUSES:
        return

    owner_user_id = await _resolve_owner(bot, chat_id)

    # Only the actual creator is allowed to invite the bot in the first
    # place — check this only on the genuine "just added" transition, not
    # on a later promotion (e.g. the creator making the bot an admin).
    just_added = old_status not in _PRESENT_STATUSES
    if just_added and owner_user_id is not None:
        adder = getattr(event, "from_user", None)
        adder_id = adder.id if adder else None
        # Only block when we could actually identify who added the bot —
        # missing from_user data must fail open, not falsely kick the bot.
        if adder_id is not None and adder_id != owner_user_id:
            logger.info("Bot added to chat %s by non-owner %s — leaving.", chat_id, adder_id)
            try:
                await bot.send_message(
                    chat_id,
                    "⚠️ Добавлять бота в чат может только владелец (создатель) чата. Бот покидает чат.",
                )
            except Exception:
                pass
            try:
                await bot.leave_chat(chat_id)
            except Exception as exc:
                logger.warning("Failed to leave chat %s after non-owner add: %s", chat_id, exc)
            return

    try:
        member_count = await bot.get_chat_member_count(chat_id)
    except Exception as exc:
        logger.warning("Cannot get member count for chat %s: %s", chat_id, exc)
        member_count = 0

    min_members = await SettingsRepository(session).get_int("chat_min_members", 250)

    chat_repo = ChatRepository(session)
    await chat_repo.upsert(
        chat_id=chat_id,
        title=event.chat.title or "",
        member_count=member_count,
        owner_user_id=owner_user_id,
        min_members=min_members,
        username=event.chat.username,
    )

    if just_added:
        # Read-only (getChat) — never generates/revokes an invite link, and
        # only ever stored once (set_invite_link_if_missing), so opening the
        # admin chat-list later never mints a fresh link.
        try:
            full_chat = await bot.get_chat(chat_id)
            if full_chat.invite_link:
                await chat_repo.set_invite_link_if_missing(chat_id, full_chat.invite_link)
        except Exception as exc:
            logger.info("Cannot read invite link for chat %s: %s", chat_id, exc)

    # Short one-time DM to the owner (item 7) — only on the genuine
    # "just connected" transition, never on later promotions/refreshes.
    if just_added and owner_user_id is not None:
        try:
            await bot.send_message(
                owner_user_id,
                "Чат успешно подключён. Управление доступно в разделе «Панель чатов» "
                "главного меню бота. Там можно настроить игры, бонусы, рассылки, "
                "спонсоров, статистику и способы заработка.",
                reply_markup=connected_instructions_kb(settings.bot_username),
            )
        except Exception as exc:
            logger.info("Cannot DM connect-instructions to owner %s: %s", owner_user_id, exc)
