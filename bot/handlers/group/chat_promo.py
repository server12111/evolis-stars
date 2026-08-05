import logging
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_promo import ChatPromoRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.group.registration import REGISTRATION_REQUIRED_TEXT, registration_required_kb
from bot.services.chat_eligibility import credit_stars, eligibility_reason
from bot.states.group import ChatOwnerPromoStates

router = Router()
logger = logging.getLogger(__name__)
settings = get_settings()

_PROMO_REDEEM_PATTERN = re.compile(r"(?i)^промокод\s+(\S+)$")


def _matches_promo_redeem(message: Message) -> bool:
    return bool(message.text and _PROMO_REDEEM_PATTERN.match(message.text.strip()))


async def start_promo_creation(callback: CallbackQuery, session: AsyncSession, state: FSMContext, chat_id: int) -> None:
    """Shared by both entry points — the group owner-menu button
    (chat_id implicit from the group the button was pressed in) and the
    private chat panel (chat_id explicit from callback_data, ownership
    re-verified by the caller). Caller must already own `chat_id`."""
    if await ChatPromoRepository(session).get_by_chat(chat_id):
        await callback.answer("❌ В этом чате уже был создан промокод.", show_alert=True)
        return
    await state.set_state(ChatOwnerPromoStates.enter_code)
    await state.update_data(chat_id=chat_id)
    await callback.message.answer("✏️ Напишите название промокода:")
    await callback.answer()


@router.callback_query(F.data == "chatmenu:promo")
async def cb_chat_promo_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    chat = await ChatRepository(session).get(chat_id)
    if not chat or callback.from_user.id != chat.owner_user_id:
        await callback.answer()
        return
    await start_promo_creation(callback, session, state, chat_id)


@router.message(ChatOwnerPromoStates.enter_code)
async def msg_chat_promo_code(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    chat_id = data.get("chat_id")
    await state.clear()
    if chat_id is None or message.from_user is None:
        return

    chat = await ChatRepository(session).get(chat_id)
    if not chat or message.from_user.id != chat.owner_user_id:
        return

    code = (message.text or "").strip()
    if not code:
        await message.reply("❌ Название промокода не может быть пустым.")
        return

    repo = ChatPromoRepository(session)
    # Re-checked here (not just at the button) in case a stale FSM state was
    # re-entered after a promo was already created — this is the atomic
    # guard, since UserMiddleware's per-user lock serializes the owner's
    # own requests but a straight "check then insert" is still the only
    # thing standing between a re-sent state and a second promo code.
    if await repo.get_by_chat(chat_id):
        await message.reply("❌ В этом чате уже был создан промокод.")
        return

    existing = await repo.get_by_code(chat_id, code)
    if existing:
        await message.reply("❌ Промокод с таким названием уже существует в этом чате.")
        return

    promo = await repo.create(chat_id, code, created_by=message.from_user.id)
    await message.reply(
        f"✅ Промокод <code>{promo.code}</code> создан!\n\n"
        f"Участники смогут ввести «промокод {promo.code}» в чате, чтобы получить награду "
        f"(при выполнении условий по времени в чате и активности).",
        parse_mode="HTML",
    )


@router.message(_matches_promo_redeem)
async def msg_chat_promo_redeem(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    chat_id = message.chat.id
    match = _PROMO_REDEEM_PATTERN.match(message.text.strip())
    code = match.group(1).strip()

    user = await session.get(User, message.from_user.id)
    if user is None:
        await message.reply(REGISTRATION_REQUIRED_TEXT, reply_markup=registration_required_kb(settings.bot_username))
        return

    settings_repo = SettingsRepository(session)
    min_messages = await settings_repo.get_int("chat_promo_min_messages", 500)

    reason = await eligibility_reason(session, chat_id, message.from_user.id, min_days=0, min_messages=min_messages)
    if reason:
        await message.reply(f"❌ Промокод недоступен: {reason}.")
        return

    repo = ChatPromoRepository(session)
    promo = await repo.get_by_code(chat_id, code)
    if not promo or not promo.is_active:
        await message.reply("❌ Такой промокод не найден.")
        return

    is_vip = bool(user.is_vip)
    reward = await settings_repo.get_float(
        "chat_promo_reward_vip" if is_vip else "chat_promo_reward",
        0.5 if is_vip else 0.3,
    )
    reward_dec = Decimal(str(reward))

    ok = await repo.use(promo, message.from_user.id, reward_dec)
    if not ok:
        await message.reply("❌ Промокод уже использован тобой или недоступен.")
        return

    await credit_stars(session, message.from_user.id, reward_dec)
    await message.reply(f"✅ Промокод активирован! Начислено <b>{reward_dec} ⭐</b>.", parse_mode="HTML")
