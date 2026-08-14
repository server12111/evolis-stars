import logging
import re
import time
from decimal import Decimal
from types import SimpleNamespace

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_bonus import (
    MAX_BONUS_SPONSORS,
    ChatBonusRepository,
    ChatBonusSponsorRepository,
)
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.group.chat_bonus import (
    bonus_mode_kb,
    bonus_sponsor_deeplink_kb,
    bonus_sponsor_reuse_kb,
    bonus_sponsors_kb,
    bonus_subscribe_kb,
)
from bot.keyboards.group.registration import REGISTRATION_REQUIRED_TEXT, registration_required_kb
from bot.services.chat_eligibility import credit_stars, debit_stars_if_enough, eligibility_reason
from bot.services.telegram_chat import is_subscribed, telegram_chat_id
from bot.states.group import ChatOwnerBonusStates, PendingSponsorAddStates

router = Router()
logger = logging.getLogger(__name__)
settings = get_settings()

_BONUS_REDEEM_PATTERN = re.compile(r"(?i)^бонус\s+(\S+)$")


def _matches_bonus_redeem(message: Message) -> bool:
    return bool(message.text and _BONUS_REDEEM_PATTERN.match(message.text.strip()))


async def _is_owner(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    chat = await ChatRepository(session).get(chat_id)
    return bool(chat and chat.owner_user_id == user_id)


_GLOBAL_USER_STATE_CHAT_ID = 0  # never a real Telegram chat id — a private chat's id always equals the user's own id, which chat_id=user_id here would collide with
# How long a single "Add sponsor" click blocks a second one before it's
# treated as abandoned rather than still in progress.
_PENDING_SPONSOR_TIMEOUT_SECONDS = 180


def _pending_sponsor_state(storage: BaseStorage, bot_id: int, user_id: int) -> FSMContext:
    """User-scoped (not chat-scoped) FSM context — the my_chat_member event
    that resolves this fires in the sponsor's own chat, not the bonus's."""
    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot_id, chat_id=_GLOBAL_USER_STATE_CHAT_ID, user_id=user_id),
    )


def _origin_state(storage: BaseStorage, bot_id: int, chat_id: int, user_id: int) -> FSMContext:
    return FSMContext(storage=storage, key=StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id))


async def start_bonus_creation(callback: CallbackQuery, state: FSMContext, chat_id: int) -> None:
    """Shared by both entry points — the group owner-menu button and the
    private chat panel. Caller must already own `chat_id`."""
    await state.set_state(ChatOwnerBonusStates.enter_code)
    await state.update_data(chat_id=chat_id)
    await callback.message.answer("✏️ Напишите название бонусного кода:")
    await callback.answer()


@router.callback_query(F.data == "chatmenu:bonus")
async def cb_chat_bonus_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    if not await _is_owner(session, chat_id, callback.from_user.id):
        await callback.answer()
        return
    await start_bonus_creation(callback, state, chat_id)


@router.message(ChatOwnerBonusStates.enter_code)
async def msg_bonus_code(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    chat_id = data.get("chat_id")
    if chat_id is None or message.from_user is None or not await _is_owner(session, chat_id, message.from_user.id):
        await state.clear()
        return

    code = (message.text or "").strip()
    if not code:
        await message.reply("❌ Название не может быть пустым.")
        return

    existing = await ChatBonusRepository(session).get_by_code(chat_id, code)
    if existing:
        await message.reply("❌ Бонусный код с таким названием уже существует.")
        return

    await state.update_data(code=code)
    await state.set_state(ChatOwnerBonusStates.enter_reward)
    await message.reply(f"Код: <b>{code}</b>\n\nВведите награду за одну активацию (число RP⭐️):", parse_mode="HTML")


@router.message(ChatOwnerBonusStates.enter_reward)
async def msg_bonus_reward(message: Message, state: FSMContext) -> None:
    try:
        reward = Decimal(str(float((message.text or "").strip().replace(",", "."))))
        if reward <= 0:
            raise ValueError
    except (ValueError, ArithmeticError):
        await message.reply("❌ Введите положительное число:")
        return
    await state.update_data(reward=str(reward))
    await state.set_state(ChatOwnerBonusStates.enter_limit)
    await message.reply(
        f"Награда: <b>{reward} RP⭐️</b>\n\nВведите количество активаций (сколько раз можно использовать код):",
        parse_mode="HTML",
    )


@router.message(ChatOwnerBonusStates.enter_limit)
async def msg_bonus_limit(message: Message, state: FSMContext) -> None:
    try:
        limit = int((message.text or "").strip())
        if limit <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.reply("❌ Введите целое положительное число:")
        return
    await state.update_data(limit=limit)
    await state.set_state(ChatOwnerBonusStates.choose_mode)
    await message.reply(
        "Выберите тип:\n"
        "🎁 <b>Обычный бонус</b> — участники сами вводят код в чате при выполнении условий.\n"
        "🏆 <b>Конкурс</b> — вы сами выбираете победителей командой в чате.",
        parse_mode="HTML",
        reply_markup=bonus_mode_kb(),
    )


@router.callback_query(ChatOwnerBonusStates.choose_mode, F.data.startswith("chatbonus:mode:"))
async def cb_bonus_mode(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    mode = callback.data.split(":")[-1]

    if mode == "contest":
        # Contests aren't built yet — stub only: no FSM transition, no
        # bonus created, no further menus. The choose_mode prompt (with
        # both mode buttons) stays on screen so the owner can still pick
        # "Обычный бонус" instead.
        await callback.message.answer("🚧 Этот раздел пока находится в разработке.")
        await callback.answer()
        return

    await state.update_data(mode=mode, sponsors=[])
    await state.set_state(ChatOwnerBonusStates.choose_sponsors)
    await callback.message.answer(
        "Добавить спонсоров\n\n"
        "Участник сможет активировать бонус только после подписки на всех спонсоров ниже "
        f"(максимум {MAX_BONUS_SPONSORS}). Можно пропустить, нажав «Готово» без добавления.",
        reply_markup=bonus_sponsors_kb(0),
    )
    await callback.answer()


async def _start_new_sponsor_flow(
    callback: CallbackQuery, state: FSMContext, bot: Bot, sponsor_type: str,
    *, purpose: str = "bonus", wall_chat_id: int | None = None,
) -> None:
    """Arms the "waiting for the owner to promote the bot" flow and sends
    the deep link — the only way to add a channel/chat the bot isn't
    already an admin in yet.

    purpose="wall" arms this same mechanism for the per-chat sponsor wall
    (bot/handlers/group/chat_sponsor_wall.py) instead of the bonus-creation
    FSM's own "sponsors" list -- try_link_pending_sponsor below branches on
    it once the owner actually promotes the bot. The caller is responsible
    for its own max-sponsors check before calling this for purpose="wall"
    (the wall's cap is per-chat, stored on the Chat row, not available
    here without a session)."""
    if purpose == "bonus":
        data = await state.get_data()
        sponsors = data.get("sponsors") or []
        if len(sponsors) >= MAX_BONUS_SPONSORS:
            await callback.answer(f"❌ Максимум {MAX_BONUS_SPONSORS} спонсора на бонус.", show_alert=True)
            return

    pending = _pending_sponsor_state(state.storage, bot.id, callback.from_user.id)
    if await pending.get_state() == PendingSponsorAddStates.awaiting_add:
        # This slot is GLOBAL per user (the my_chat_member confirmation
        # fires in the sponsor's own chat, with no way to tell which of
        # two simultaneous "Add sponsor" clicks it belongs to), so a
        # second click here — even from a different bonus flow in a
        # different chat — would silently overwrite the first one's
        # origin_chat_id/sponsor_type. The first promotion that then
        # arrives would get misattributed to the wrong bonus. Blocking
        # the second click instead of overwriting removes the ambiguity.
        # A timeout still lets a genuinely abandoned attempt (the owner
        # never promoted the bot anywhere) unblock itself later.
        pending_data = await pending.get_data()
        armed_at = pending_data.get("armed_at", 0)
        if time.time() - armed_at < _PENDING_SPONSOR_TIMEOUT_SECONDS:
            await callback.answer(
                "⚠️ Сначала завершите добавление предыдущего спонсора "
                "(назначьте бота администратором там) или подождите 3 минуты.",
                show_alert=True,
            )
            return
    await pending.set_state(PendingSponsorAddStates.awaiting_add)
    # origin_chat_id must be the chat this flow's own FSM state is keyed
    # under (wherever this button was actually pressed — the managed
    # group when started from /EvolisOpen, or the owner's private chat
    # when started from the chat panel), NOT the "chat_id" data value
    # (the target group being managed), which only coincided with it
    # for the group-triggered flow. Getting this wrong here means
    # try_link_pending_sponsor would write the confirmed sponsor into a
    # different FSM slot than cb_bonus_sponsors_done later reads from.
    await pending.update_data(
        sponsor_type=sponsor_type, origin_chat_id=callback.message.chat.id, armed_at=time.time(),
        purpose=purpose, wall_chat_id=wall_chat_id,
    )

    label = "канал" if sponsor_type == "channel" else "чат"
    await callback.message.answer(
        f"Добавьте бота администратором в нужный {label} по кнопке ниже — я запрошу только права, "
        f"необходимые для проверки подписки участников. Как только вы это сделаете, спонсор "
        f"автоматически появится в этом списке.\n\n"
        f"Бот уже администратор там? Пришлите мне его @username (или перешлите любое "
        f"сообщение оттуда) — я сам проверю права.",
        reply_markup=bonus_sponsor_deeplink_kb(settings.bot_username, sponsor_type),
    )
    await callback.answer()


@router.callback_query(ChatOwnerBonusStates.choose_sponsors, F.data.startswith("chatbonus:addsponsor:"))
async def cb_bonus_add_sponsor_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot,
) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    parts = callback.data.split(":")

    # "chatbonus:addsponsor:new:<type>" — the "add a brand-new channel/chat"
    # button on the reuse-list screen below, always goes straight to the
    # deep-link flow without re-showing that same list.
    if len(parts) == 4 and parts[2] == "new":
        sponsor_type = parts[3]
        if sponsor_type not in ("channel", "chat"):
            await callback.answer()
            return
        await _start_new_sponsor_flow(callback, state, bot, sponsor_type)
        return

    sponsor_type = parts[-1]
    if sponsor_type not in ("channel", "chat"):
        await callback.answer()
        return

    data = await state.get_data()
    sponsors = data.get("sponsors") or []
    if len(sponsors) >= MAX_BONUS_SPONSORS:
        await callback.answer(f"❌ Максимум {MAX_BONUS_SPONSORS} спонсора на бонус.", show_alert=True)
        return

    # A channel/chat already used as a sponsor in one of this owner's
    # earlier bonuses has the bot admin in it already — Telegram's
    # "promote to admin" deep link fires no my_chat_member event (no
    # status change) when the bot is already an admin there, so that flow
    # would silently do nothing. Offer to reuse it directly instead.
    already_added = {s["chat_id"] for s in sponsors}
    reusable = [
        sponsor
        for sponsor in await ChatBonusSponsorRepository(session).list_previously_used_by_owner(
            callback.from_user.id, sponsor_type,
        )
        if sponsor.sponsor_chat_id not in already_added
    ]
    if reusable:
        label = "каналов" if sponsor_type == "channel" else "чатов"
        await callback.message.answer(
            f"У вас уже есть добавленные {label} (из других бонусов) — выберите один "
            f"из них или добавьте новый:",
            reply_markup=bonus_sponsor_reuse_kb(reusable, sponsor_type),
        )
        await callback.answer()
        return

    await _start_new_sponsor_flow(callback, state, bot, sponsor_type)


@router.callback_query(ChatOwnerBonusStates.choose_sponsors, F.data.startswith("chatbonus:reuse:"))
async def cb_bonus_add_sponsor_reuse(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    sponsor_type = parts[2]
    try:
        chat_id = int(parts[3])
    except ValueError:
        await callback.answer()
        return

    data = await state.get_data()
    sponsors = data.get("sponsors") or []
    if len(sponsors) >= MAX_BONUS_SPONSORS:
        await callback.answer(f"❌ Максимум {MAX_BONUS_SPONSORS} спонсора на бонус.", show_alert=True)
        return
    if any(s["chat_id"] == chat_id for s in sponsors):
        await callback.answer("⚠️ Этот чат уже добавлен как спонсор.", show_alert=True)
        return

    # The bot could have been removed/demoted there since it was last used
    # as a sponsor — re-verify live rather than trusting the saved row.
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
    except Exception:
        member = None
    if member is None or member.status != "administrator":
        await callback.answer(
            "⚠️ Бот больше не администратор там — добавьте заново кнопкой «Новый канал/чат».",
            show_alert=True,
        )
        return

    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or chat.username or ""
        username = chat.username
    except Exception:
        title, username = "", None

    sponsors.append({"chat_id": chat_id, "type": sponsor_type, "title": title, "username": username})
    await state.update_data(sponsors=sponsors)
    await callback.message.answer(
        f"✅ Спонсор «{title or chat_id}» добавлен ({len(sponsors)}/{MAX_BONUS_SPONSORS}).",
        reply_markup=bonus_sponsors_kb(len(sponsors)),
    )
    await callback.answer()


@router.callback_query(ChatOwnerBonusStates.choose_sponsors, F.data == "chatbonus:sponsors:done")
async def cb_bonus_sponsors_done(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    data = await state.get_data()
    chat_id = data.get("chat_id")
    if chat_id is None or not await _is_owner(session, chat_id, callback.from_user.id):
        await state.clear()
        await callback.answer()
        return

    code = data["code"]
    reward = Decimal(data["reward"])
    limit = int(data["limit"])
    mode = data.get("mode", "self_serve")
    sponsors = data.get("sponsors") or []
    await state.clear()

    settings_repo = SettingsRepository(session)
    commission_rate = Decimal(str(await settings_repo.get_float("chat_bonus_commission", 0.07)))
    total_charged = (reward * limit * (1 + commission_rate)).quantize(Decimal("0.01"))

    debited = await debit_stars_if_enough(session, callback.from_user.id, total_charged)
    if not debited:
        await callback.message.answer(
            f"❌ Недостаточно звёзд на балансе. Нужно <b>{total_charged} RP⭐️</b> "
            f"({reward} RP⭐️ × {limit} активаций + {int(commission_rate * 100)}% комиссии).",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    bonus = await ChatBonusRepository(session).create(
        chat_id=chat_id,
        code=code,
        reward_amount=reward,
        usage_limit=limit,
        commission_rate=commission_rate,
        mode=mode,
        min_days_in_chat=0,
        min_messages=0,
        condition_note=None,
        created_by=callback.from_user.id,
    )
    if bonus is None:
        # A bonus with this exact code already exists in this chat (race
        # with another submission of the same flow) — the balance was
        # already debited above, so it must be refunded since no bonus
        # was actually created.
        await credit_stars(session, callback.from_user.id, total_charged)
        await callback.message.answer("❌ Бонусный код с таким названием уже существует.")
        await callback.answer()
        return

    sponsor_repo = ChatBonusSponsorRepository(session)
    for sponsor in sponsors[:MAX_BONUS_SPONSORS]:
        await sponsor_repo.add(
            bonus.id, sponsor["chat_id"], sponsor["type"], sponsor.get("title", ""), sponsor.get("username"),
        )

    mode_label = "Конкурс" if mode == "contest" else "Обычный бонус"
    how_to = (
        f"Участники вводят «бонус {bonus.code}» в чате."
        if mode == "self_serve"
        else f"Чтобы выбрать победителя, ответьте на его сообщение командой «выбрать {bonus.code}»."
    )
    sponsors_line = ""
    if sponsors:
        names = ", ".join(s.get("title") or "спонсор" for s in sponsors[:MAX_BONUS_SPONSORS])
        sponsors_line = f"\nСпонсоры: {names}"
    await callback.message.answer(
        f"✅ <b>{mode_label} создан!</b>\n\n"
        f"Код: <code>{bonus.code}</code>\n"
        f"Награда: <b>{reward} RP⭐️</b> × {limit} активаций\n"
        f"Списано с баланса: <b>{total_charged} RP⭐️</b>{sponsors_line}\n\n"
        f"{how_to}",
        parse_mode="HTML",
    )
    await callback.answer()


async def try_link_pending_sponsor(
    bot: Bot, storage: BaseStorage, event: ChatMemberUpdated, session: AsyncSession,
) -> bool:
    """Called from onboarding.py's my_chat_member handler before its own
    logic. Returns True if this event was consumed as a sponsor-add (the
    caller should skip normal chat-registration handling for it).

    session is only actually used by the purpose="wall" branch (persisting
    directly to ChatSponsorWallSponsor) -- the purpose="bonus" branch below
    is unchanged and still only touches FSM state."""
    adder = getattr(event, "from_user", None)
    if adder is None:
        return False

    pending = _pending_sponsor_state(storage, bot.id, adder.id)
    if await pending.get_state() != PendingSponsorAddStates.awaiting_add:
        return False

    pending_data = await pending.get_data()
    sponsor_type = pending_data.get("sponsor_type")
    origin_chat_id = pending_data.get("origin_chat_id")
    purpose = pending_data.get("purpose", "bonus")
    wall_chat_id = pending_data.get("wall_chat_id")
    if sponsor_type not in ("channel", "chat") or origin_chat_id is None:
        await pending.clear()
        return False
    if purpose == "wall" and wall_chat_id is None:
        await pending.clear()
        return False

    # Only the chat this specific deep link was opened in matters — ignore
    # unrelated membership changes for the adder in other chats/channels.
    # The marker stays armed (not cleared) so promoting the bot moments
    # later re-fires this handler and completes the link.
    if event.new_chat_member.status != "administrator":
        try:
            await bot.send_message(
                event.chat.id,
                "⚠️ Чтобы добавить этот чат как спонсора, назначьте бота администратором "
                "(без этого проверка подписки участников не будет работать).",
            )
        except Exception:
            pass
        return True

    actual_is_channel = event.chat.type == "channel"
    expected_is_channel = sponsor_type == "channel"
    if actual_is_channel != expected_is_channel:
        label = "канал" if sponsor_type == "channel" else "чат/группу"
        try:
            await bot.send_message(event.chat.id, f"⚠️ Это не {label}. Спонсор не добавлен.")
        except Exception:
            pass
        return True

    if purpose == "wall":
        from bot.handlers.group.chat_sponsor_wall import link_pending_wall_sponsor
        return await link_pending_wall_sponsor(
            bot, session, pending, event, wall_chat_id, origin_chat_id, sponsor_type,
        )

    origin = _origin_state(storage, bot.id, origin_chat_id, adder.id)
    origin_data = await origin.get_data()
    sponsors = origin_data.get("sponsors") or []
    if len(sponsors) >= MAX_BONUS_SPONSORS:
        try:
            await bot.send_message(event.chat.id, f"⚠️ Уже добавлено максимум {MAX_BONUS_SPONSORS} спонсора.")
        except Exception:
            pass
        await pending.clear()
        return True
    if any(s["chat_id"] == event.chat.id for s in sponsors):
        try:
            await bot.send_message(event.chat.id, "⚠️ Этот чат уже добавлен как спонсор.")
        except Exception:
            pass
        await pending.clear()
        return True

    sponsors.append({
        "chat_id": event.chat.id,
        "type": sponsor_type,
        "title": event.chat.title or event.chat.username or "",
        "username": event.chat.username,
    })
    await origin.update_data(sponsors=sponsors)
    await pending.clear()

    if sponsor_type == "chat":
        # Only for a regular chat/group — posting into a channel is more
        # intrusive/public, so that one stays silent and only the owner
        # gets notified back in the bonus-creation chat below.
        try:
            await bot.send_message(event.chat.id, "✅ Этот чат добавлен как спонсор бонуса.")
        except Exception:
            pass
    try:
        await bot.send_message(
            origin_chat_id,
            f"✅ Спонсор «{event.chat.title or event.chat.username}» добавлен ({len(sponsors)}/{MAX_BONUS_SPONSORS}).",
            reply_markup=bonus_sponsors_kb(len(sponsors)),
        )
    except Exception:
        pass
    return True


def _resolve_forwarded_chat_id(message: Message) -> int | None:
    """Bot API 7.0+ reports the forward source via forward_origin (a
    MessageOriginChannel for a channel post, or a MessageOriginChat for a
    message forwarded on behalf of a group's own identity) -- the older
    forward_from_chat field it replaced is no longer populated by current
    clients, but is kept here as a fallback for anything still relying on
    it."""
    origin = message.forward_origin
    if origin is not None:
        if origin.type == "channel":
            return origin.chat.id
        if origin.type == "chat":
            return origin.sender_chat.id
        return None
    if message.forward_from_chat is not None:
        return message.forward_from_chat.id
    return None


async def try_confirm_pending_sponsor_manually(
    message: Message, session: AsyncSession, bot: Bot, storage: BaseStorage,
) -> bool:
    """Fallback for the exact gap try_link_pending_sponsor can't cover:
    Telegram's "promote to admin" deep link fires no my_chat_member event
    when the bot is ALREADY an admin in the target channel/chat for some
    unrelated reason (never previously used as a sponsor, so
    list_previously_used_by_owner has nothing to offer as a reuse
    candidate either) -- the pending add would otherwise wait forever
    with no way to ever complete. Lets the owner instead forward a
    message from the channel/chat, or send its @username/t.me link, and
    verifies both that the bot is an admin there AND that the requester
    themselves is (same proof of control the my_chat_member path gets for
    free from Telegram only ever firing that event to whoever actually
    promoted the bot) -- without the second check, anyone with a pending
    marker armed could claim any chat/channel the bot happens to already
    admin for an unrelated reason as their own sponsor.

    Returns False (caller must let the message fall through to normal
    handling) whenever nothing is pending or the message doesn't look
    like a channel/chat reference at all -- only a message that DOES look
    like an attempt is ever answered here."""
    if message.from_user is None:
        return False
    pending = _pending_sponsor_state(storage, bot.id, message.from_user.id)
    if await pending.get_state() != PendingSponsorAddStates.awaiting_add:
        return False

    raw_target: int | str | None = _resolve_forwarded_chat_id(message)
    if raw_target is None and message.text:
        raw_target = telegram_chat_id(message.text)

    if raw_target is None:
        # Not a recognizable channel/chat reference (e.g. the owner is
        # typing something else entirely, unrelated to this pending add)
        # -- do not swallow it.
        return False

    try:
        target_chat = await bot.get_chat(raw_target)
    except Exception:
        return False

    try:
        member = await bot.get_chat_member(target_chat.id, bot.id)
    except Exception:
        member = None
    if member is None or member.status != "administrator":
        await message.answer(
            "⚠️ Бот не администратор там. Сначала назначьте его администратором "
            "(кнопкой выше), затем пришлите @username ещё раз или перешлите сообщение."
        )
        return True

    try:
        requester = await bot.get_chat_member(target_chat.id, message.from_user.id)
    except Exception:
        requester = None
    if requester is None or requester.status not in ("administrator", "creator"):
        await message.answer("⚠️ Вы должны быть администратором этого канала/чата, чтобы добавить его спонсором.")
        return True

    fake_event = SimpleNamespace(
        chat=SimpleNamespace(
            id=target_chat.id,
            type=target_chat.type,
            title=target_chat.title or "",
            username=target_chat.username,
        ),
        new_chat_member=SimpleNamespace(status="administrator"),
        old_chat_member=SimpleNamespace(status="left"),
        from_user=message.from_user,
    )
    return await try_link_pending_sponsor(bot, storage, fake_event, session)


@router.message(_matches_bonus_redeem)
async def msg_bonus_redeem(message: Message, session: AsyncSession, bot: Bot) -> None:
    if message.from_user is None or message.text is None:
        return
    match = _BONUS_REDEEM_PATTERN.match(message.text.strip())
    code = match.group(1).strip()
    chat_id = message.chat.id

    user = await session.get(User, message.from_user.id)
    if user is None:
        await message.reply(REGISTRATION_REQUIRED_TEXT, reply_markup=registration_required_kb(settings.bot_username))
        return

    repo = ChatBonusRepository(session)
    bonus = await repo.get_by_code(chat_id, code)
    if not bonus or not bonus.is_active or bonus.mode != "self_serve":
        await message.reply("❌ Такой бонусный код не найден.")
        return

    reason = await eligibility_reason(
        session, chat_id, message.from_user.id, bonus.min_days_in_chat, bonus.min_messages
    )
    if reason:
        await message.reply(f"❌ Бонус недоступен: {reason}.")
        return

    sponsors = await ChatBonusSponsorRepository(session).list_for_bonus(bonus.id)
    if sponsors:
        unsubscribed = await _unsubscribed_sponsors(bot, sponsors, message.from_user.id)
        if unsubscribed:
            await message.reply(
                "Вы не подписаны на спонсоров.\n\n" + _sponsors_list_text(sponsors),
                reply_markup=bonus_subscribe_kb(sponsors, bonus.id),
            )
            return

    async def _notify(text: str) -> None:
        await message.reply(text, parse_mode="HTML")

    await _finalize_bonus_redeem(session, bonus, message.from_user.id, _notify)


def _sponsors_list_text(sponsors: list) -> str:
    """Text fallback for every sponsor — bonus_subscribe_kb only renders a
    clickable button for sponsors with a public username, so a private
    chat/channel without one would otherwise be completely invisible to
    the user (no button, no name, no way to find it)."""
    lines = []
    for sponsor in sponsors:
        name = sponsor.title or sponsor.username or "Спонсор"
        lines.append(f"📢 {name}" if sponsor.username else f"📢 {name} (у владельца бонуса)")
    return "\n".join(lines)


async def _unsubscribed_sponsors(bot: Bot, sponsors: list, user_id: int) -> list:
    unsubscribed = []
    for sponsor in sponsors:
        try:
            member = await bot.get_chat_member(sponsor.sponsor_chat_id, user_id)
        except Exception:
            unsubscribed.append(sponsor)
            continue
        if not is_subscribed(member):
            unsubscribed.append(sponsor)
    return unsubscribed


async def _finalize_bonus_redeem(session: AsyncSession, bonus, user_id: int, notify) -> None:
    repo = ChatBonusRepository(session)
    ok = await repo.redeem(bonus, user_id)
    text = (
        f"✅ Бонус получен! Начислено <b>{bonus.reward_amount} RP⭐️</b>."
        if ok
        else "❌ Бонус уже получен тобой или лимит активаций исчерпан."
    )
    if ok:
        await credit_stars(session, user_id, bonus.reward_amount)
    await notify(text)


@router.callback_query(F.data.startswith("chatbonus:check:"))
async def cb_bonus_check_sponsors(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    try:
        bonus_id = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer()
        return

    repo = ChatBonusRepository(session)
    bonus = await repo.get_by_id(bonus_id)
    if not bonus or not bonus.is_active or bonus.mode != "self_serve":
        await callback.answer("❌ Бонус больше недоступен.", show_alert=True)
        return

    user = await session.get(User, callback.from_user.id)
    if user is None:
        await callback.answer("Сначала зарегистрируйтесь в личных сообщениях.", show_alert=True)
        return

    sponsors = await ChatBonusSponsorRepository(session).list_for_bonus(bonus.id)
    unsubscribed = await _unsubscribed_sponsors(bot, sponsors, callback.from_user.id)
    if unsubscribed:
        await callback.answer("Вы ещё не подписаны на всех спонсоров.", show_alert=True)
        return

    if bonus.used_count >= bonus.usage_limit:
        await callback.answer("❌ Лимит активаций исчерпан.", show_alert=True)
        return

    async def _notify(text: str) -> None:
        await callback.message.answer(text, parse_mode="HTML")

    await _finalize_bonus_redeem(session, bonus, callback.from_user.id, _notify)
    await callback.answer()


@router.message(F.text.regexp(r"(?i)^выбрать\s+(\S+)$"))
async def msg_bonus_pick_winner(message: Message, session: AsyncSession) -> None:
    if (
        message.from_user is None
        or message.text is None
        or message.reply_to_message is None
        or message.reply_to_message.from_user is None
    ):
        return
    chat_id = message.chat.id
    if not await _is_owner(session, chat_id, message.from_user.id):
        return

    code = message.text.split(maxsplit=1)[1].strip()
    repo = ChatBonusRepository(session)
    bonus = await repo.get_by_code(chat_id, code)
    if not bonus or not bonus.is_active or bonus.mode != "contest":
        await message.reply("❌ Такой конкурс не найден.")
        return

    winner = message.reply_to_message.from_user
    ok = await repo.redeem(bonus, winner.id, awarded_by=message.from_user.id)
    if not ok:
        await message.reply("❌ Не удалось начислить: лимит исчерпан или этот участник уже выигрывал.")
        return

    await credit_stars(session, winner.id, bonus.reward_amount)
    winner_name = winner.first_name or "участник"
    await message.reply(
        f"🏆 Победитель выбран! {winner_name} получает <b>{bonus.reward_amount} RP⭐️</b>.",
        parse_mode="HTML",
    )
