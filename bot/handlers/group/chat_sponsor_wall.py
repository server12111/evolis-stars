import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import Chat, User
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_sponsor_wall import ChatSponsorWallRepository
from bot.handlers.group.chat_bonus import _start_new_sponsor_flow
from bot.services.telegram_chat import is_subscribed

router = Router()
logger = logging.getLogger(__name__)
settings = get_settings()

MAX_WALL_SPONSORS_HARD_CAP = 20


def _back_to_wall_kb(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 К спонсор-стене", callback_data=f"mychats:wall:{chat_id}"))
    return builder.as_markup()


async def start_wall_sponsor_flow(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot,
    chat_id: int, sponsor_type: str,
) -> None:
    """Entry point for the chat panel's "Добавить спонсора" button (see
    bot/handlers/mychats.py) -- arms the shared pending-sponsor-add
    mechanism (bot/handlers/group/chat_bonus.py) for purpose="wall"."""
    chat = await ChatRepository(session).get(chat_id)
    if chat is None or chat.owner_user_id != callback.from_user.id:
        await callback.answer("❌ Этот чат вам не принадлежит.", show_alert=True)
        return

    wall_repo = ChatSponsorWallRepository(session)
    current = len(await wall_repo.list_active(chat_id))
    if current >= chat.sponsor_wall_max_sponsors:
        await callback.answer(
            f"❌ Достигнут лимит спонсоров ({chat.sponsor_wall_max_sponsors}). "
            f"Увеличьте лимит или удалите неактуальный спонсор.",
            show_alert=True,
        )
        return

    # list_all, not list_active: a sponsor previously added to THIS chat
    # and since toggled off must not be offered as "reusable" here too --
    # wall_repo.add() below would reject it outright on the unique
    # constraint (it's already a row, just inactive), producing a
    # confusing generic failure instead of steering the owner to the
    # wall panel's own toggle button to reactivate it.
    already_added = {s.sponsor_chat_id for s in await wall_repo.list_all(chat_id)}
    from bot.database.repositories.chat_bonus import ChatBonusSponsorRepository

    reusable = [
        s for s in await wall_repo.list_previously_used_by_owner(callback.from_user.id, sponsor_type)
        if s.sponsor_chat_id not in already_added
    ] + [
        s for s in await ChatBonusSponsorRepository(session).list_previously_used_by_owner(
            callback.from_user.id, sponsor_type,
        )
        if s.sponsor_chat_id not in already_added
    ]
    if reusable:
        seen: set[int] = set()
        builder = InlineKeyboardBuilder()
        for sponsor in reusable:
            if sponsor.sponsor_chat_id in seen:
                continue
            seen.add(sponsor.sponsor_chat_id)
            title = sponsor.title or sponsor.username or str(sponsor.sponsor_chat_id)
            builder.row(InlineKeyboardButton(
                text=f"♻️ {title}",
                callback_data=f"chatwall:reuse:{chat_id}:{sponsor_type}:{sponsor.sponsor_chat_id}",
            ))
        builder.row(InlineKeyboardButton(
            text="➕ Новый канал/чат", callback_data=f"chatwall:addnew:{chat_id}:{sponsor_type}",
        ))
        label = "каналов" if sponsor_type == "channel" else "чатов"
        await callback.message.answer(
            f"У вас уже есть {label}, где бот уже администратор — выберите один из них или добавьте новый:",
            reply_markup=builder.as_markup(),
        )
        await callback.answer()
        return

    await _start_new_sponsor_flow(callback, state, bot, sponsor_type, purpose="wall", wall_chat_id=chat_id)


async def cb_wall_sponsor_addnew(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """Registered on mychats.py's own (private-chat) router, same pattern
    as chat_bonus.py's cb_bonus_add_sponsor_start/reuse -- this screen only
    ever lives in the private chat panel, never the group owner-menu, so
    it must NOT be decorated with @router.callback_query here (this
    module's router is only wired into the group-filtered router in
    bot/handlers/group/__init__.py and would never see a private-chat
    callback)."""
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        chat_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    sponsor_type = parts[3]
    if sponsor_type not in ("channel", "chat"):
        await callback.answer()
        return
    chat = await ChatRepository(session).get(chat_id)
    if chat is None or chat.owner_user_id != callback.from_user.id:
        await callback.answer("❌ Этот чат вам не принадлежит.", show_alert=True)
        return
    await _start_new_sponsor_flow(callback, state, bot, sponsor_type, purpose="wall", wall_chat_id=chat_id)


async def cb_wall_sponsor_reuse(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Same private-chat-only registration note as cb_wall_sponsor_addnew
    above."""
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        chat_id = int(parts[2])
        sponsor_chat_id = int(parts[4])
    except ValueError:
        await callback.answer()
        return
    sponsor_type = parts[3]
    if sponsor_type not in ("channel", "chat"):
        await callback.answer()
        return

    chat = await ChatRepository(session).get(chat_id)
    if chat is None or chat.owner_user_id != callback.from_user.id:
        await callback.answer("❌ Этот чат вам не принадлежит.", show_alert=True)
        return

    # The bot could have been removed/demoted there since it was last used
    # as a sponsor -- re-verify live rather than trusting the saved row.
    try:
        member = await bot.get_chat_member(sponsor_chat_id, bot.id)
    except Exception:
        member = None
    if member is None or member.status != "administrator":
        await callback.answer(
            "⚠️ Бот больше не администратор там — добавьте заново кнопкой «Новый канал/чат».",
            show_alert=True,
        )
        return

    try:
        target = await bot.get_chat(sponsor_chat_id)
        title = target.title or target.username or ""
        username = target.username
    except Exception:
        title, username = "", None

    wall_repo = ChatSponsorWallRepository(session)
    sponsor = await wall_repo.add(
        chat_id, sponsor_chat_id, sponsor_type, title, username,
        max_sponsors=chat.sponsor_wall_max_sponsors,
    )
    if sponsor is None:
        await callback.answer("❌ Этот чат уже добавлен или лимит спонсоров исчерпан.", show_alert=True)
        return

    count = len(await wall_repo.list_active(chat_id))
    await callback.message.answer(
        f"✅ Спонсор «{title or sponsor_chat_id}» добавлен ({count}/{chat.sponsor_wall_max_sponsors}).",
        reply_markup=_back_to_wall_kb(chat_id),
    )
    await callback.answer()


async def link_pending_wall_sponsor(
    bot: Bot, session: AsyncSession, pending: FSMContext, event: ChatMemberUpdated,
    wall_chat_id: int, origin_chat_id: int, sponsor_type: str,
) -> bool:
    """The purpose="wall" branch of try_link_pending_sponsor
    (bot/handlers/group/chat_bonus.py) -- called once the owner has
    already been confirmed as having promoted the bot to admin in
    event.chat, and the sponsor/channel-vs-chat type already matches."""
    chat = await session.get(Chat, wall_chat_id)
    await pending.clear()
    if chat is None:
        return True

    wall_repo = ChatSponsorWallRepository(session)
    sponsor = await wall_repo.add(
        wall_chat_id, event.chat.id, sponsor_type,
        event.chat.title or event.chat.username or "", event.chat.username,
        max_sponsors=chat.sponsor_wall_max_sponsors,
    )
    if sponsor is None:
        try:
            await bot.send_message(event.chat.id, "⚠️ Уже добавлено или лимит спонсоров исчерпан.")
        except Exception:
            pass
        return True

    if sponsor_type == "chat":
        try:
            await bot.send_message(event.chat.id, "✅ Этот чат добавлен как спонсор стены.")
        except Exception:
            pass

    count = len(await wall_repo.list_active(wall_chat_id))
    try:
        await bot.send_message(
            origin_chat_id,
            f"✅ Спонсор «{event.chat.title or event.chat.username}» добавлен "
            f"({count}/{chat.sponsor_wall_max_sponsors}).",
            reply_markup=_back_to_wall_kb(wall_chat_id),
        )
    except Exception:
        pass
    return True


def wall_subscribe_kb(
    sponsors: list, chat_id: int, integration_items: list[dict] | None = None,
) -> InlineKeyboardMarkup:
    # Known limitation, inherited from bonus_subscribe_kb's identical
    # shape (bot/keyboards/group/chat_bonus.py): a sponsor with no public
    # username (a private channel/chat) gets no join button here, even
    # though membership in it is still required to pass the wall. Same
    # gap as the pre-existing bonus-sponsor flow -- an owner should stick
    # to public channels/chats as wall sponsors until this is addressed.
    builder = InlineKeyboardBuilder()
    for sponsor in sponsors:
        label = f"📢 {sponsor.title or sponsor.username or 'Спонсор'}"
        if sponsor.username:
            builder.row(InlineKeyboardButton(text=label, url=f"https://t.me/{sponsor.username}"))
    # Ad-network offers (tgrass/botohub/traffy/flyerhub) -- paid, unlike
    # the owner sponsors above. Same item shape sponsor_wave_markup already
    # renders for the /start wall (provider/url/name).
    for item in integration_items or []:
        url = item.get("url")
        if not url:
            continue
        label = f"⭐️ {item.get('name') or 'Спонсор'}"
        builder.row(InlineKeyboardButton(text=label, url=str(url)))
    builder.row(InlineKeyboardButton(text="✅ Проверить", callback_data=f"chatwall:check:{chat_id}"))
    return builder.as_markup()


@router.callback_query(F.data.startswith("chatwall:check:"))
async def cb_wall_check(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    try:
        chat_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    wall_repo = ChatSponsorWallRepository(session)
    active = await wall_repo.list_active(chat_id)
    if not active:
        await callback.answer("✅ Стена больше не активна.", show_alert=True)
        return

    completed_ids = await wall_repo.completed_sponsor_ids([s.id for s in active], callback.from_user.id)
    still_missing = []
    for sponsor in active:
        if sponsor.id in completed_ids:
            continue
        try:
            member = await bot.get_chat_member(sponsor.sponsor_chat_id, callback.from_user.id)
            subscribed = is_subscribed(member)
        except Exception:
            subscribed = False
        if subscribed:
            # No RP for owner-added sponsors -- unfunded, the chat owner's
            # own promotion. mark_completed is still needed to satisfy the
            # gate (see ChatSponsorWallMiddleware).
            await wall_repo.mark_completed(sponsor.id, callback.from_user.id)
        else:
            still_missing.append(sponsor)

    still_missing_integration: list[dict] = []
    integration_unavailable = False
    newly_completed = 0
    db_user = await session.get(User, callback.from_user.id)
    if db_user is not None:
        from bot.services.chat_wall_integrations import evaluate_and_credit_integration_wave

        wave_state, newly_completed = await evaluate_and_credit_integration_wave(callback, db_user, session, bot)
        if wave_state.status == "pending":
            still_missing_integration = wave_state.items or []
        elif wave_state.status == "unavailable":
            # A provider outage must not read as "nothing missing" -- keep
            # blocking rather than silently passing the paid wall.
            integration_unavailable = True

    if not still_missing and not still_missing_integration and not integration_unavailable:
        try:
            await callback.message.edit_text("✅ Теперь можно писать в этом чате!")
        except Exception:
            pass
        await callback.answer("✅ Готово!" + (f" +{newly_completed} RP⭐️" if newly_completed else ""))
        return

    owner_names = ", ".join(escape(s.title or s.username or "спонсор") for s in still_missing)
    integration_names = ", ".join(
        escape(str(item.get("name") or item.get("url") or "спонсор")) for item in still_missing_integration
    )
    names = ", ".join(part for part in (owner_names, integration_names) if part)
    message_parts = []
    if names:
        message_parts.append(f"❌ Ещё не подписан(а) на: {names}")
    if integration_unavailable:
        message_parts.append("⚠️ Проверка бот-спонсоров временно недоступна, попробуй позже.")
    await callback.answer(" ".join(message_parts), show_alert=True)
