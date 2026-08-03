import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_bonus import ChatBonusRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.group.chat_bonus import bonus_mode_kb
from bot.services.chat_eligibility import credit_stars, debit_stars_if_enough, eligibility_reason
from bot.states.group import ChatOwnerBonusStates

router = Router()
logger = logging.getLogger(__name__)


async def _is_owner(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    chat = await ChatRepository(session).get(chat_id)
    return bool(chat and chat.owner_user_id == user_id)


@router.callback_query(F.data == "chatmenu:bonus")
async def cb_chat_bonus_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    if not await _is_owner(session, chat_id, callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(ChatOwnerBonusStates.enter_code)
    await state.update_data(chat_id=chat_id)
    await callback.message.answer("✏️ Напишите название бонусного кода:")
    await callback.answer()


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
    await message.reply(f"Код: <b>{code}</b>\n\nВведите награду за одну активацию (число ⭐):", parse_mode="HTML")


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
        f"Награда: <b>{reward} ⭐</b>\n\nВведите количество активаций (сколько раз можно использовать код):",
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
    await state.update_data(mode=mode)
    await state.set_state(ChatOwnerBonusStates.enter_conditions)
    await callback.message.answer(
        "Дополнительное условие для активации (необязательно, только для информации участникам) — "
        "например «подписаться на канал X».\nОтправьте текст условия или «-», если условий нет."
    )
    await callback.answer()


@router.message(ChatOwnerBonusStates.enter_conditions)
async def msg_bonus_conditions(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    chat_id = data.get("chat_id")
    if chat_id is None or message.from_user is None or not await _is_owner(session, chat_id, message.from_user.id):
        await state.clear()
        return

    note_raw = (message.text or "").strip()
    condition_note = None if note_raw in ("-", "") else note_raw

    code = data["code"]
    reward = Decimal(data["reward"])
    limit = int(data["limit"])
    mode = data.get("mode", "self_serve")
    await state.clear()

    settings_repo = SettingsRepository(session)
    commission_rate = Decimal(str(await settings_repo.get_float("chat_bonus_commission", 0.07)))
    total_charged = (reward * limit * (1 + commission_rate)).quantize(Decimal("0.01"))

    debited = await debit_stars_if_enough(session, message.from_user.id, total_charged)
    if not debited:
        await message.reply(
            f"❌ Недостаточно звёзд на балансе. Нужно <b>{total_charged} ⭐</b> "
            f"({reward} ⭐ × {limit} активаций + {int(commission_rate * 100)}% комиссии).",
            parse_mode="HTML",
        )
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
        condition_note=condition_note,
        created_by=message.from_user.id,
    )

    mode_label = "Конкурс" if mode == "contest" else "Обычный бонус"
    how_to = (
        f"Участники вводят «бонус {bonus.code}» в чате."
        if mode == "self_serve"
        else f"Чтобы выбрать победителя, ответьте на его сообщение командой «выбрать {bonus.code}»."
    )
    condition_line = f"\nУсловие: {condition_note}" if condition_note else ""
    await message.reply(
        f"✅ <b>{mode_label} создан!</b>\n\n"
        f"Код: <code>{bonus.code}</code>\n"
        f"Награда: <b>{reward} ⭐</b> × {limit} активаций\n"
        f"Списано с баланса: <b>{total_charged} ⭐</b>{condition_line}\n\n"
        f"{how_to}",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(r"(?i)^бонус\s+(\S+)$"))
async def msg_bonus_redeem(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or message.text is None:
        return
    chat_id = message.chat.id
    code = message.text.split(maxsplit=1)[1].strip()

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

    ok = await repo.redeem(bonus, message.from_user.id)
    if not ok:
        await message.reply("❌ Бонус уже получен тобой или лимит активаций исчерпан.")
        return

    await credit_stars(session, message.from_user.id, bonus.reward_amount)
    await message.reply(f"✅ Бонус получен! Начислено <b>{bonus.reward_amount} ⭐</b>.", parse_mode="HTML")


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
        f"🏆 Победитель выбран! {winner_name} получает <b>{bonus.reward_amount} ⭐</b>.",
        parse_mode="HTML",
    )
