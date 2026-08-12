import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import Chat, User
from bot.database.repositories.virus import VirusInfectionRepository
from bot.keyboards.group.registration import REGISTRATION_REQUIRED_TEXT, registration_required_kb
from bot.keyboards.group.virus import virus_ammo_kb, virus_cure_kb
from bot.services.chat_eligibility import credit_stars, debit_stars_if_enough
from bot.services.virus_game import (
    AMMO_COST,
    COOLDOWN,
    HERBS_COST,
    MEDICINE_COST,
    VIRUS_TYPES,
    infection_payout,
    roll_ammo_success,
    roll_herbs_success,
    roll_infect_success,
    roll_virus_type,
)

settings = get_settings()
router = Router()

_ATTACK_PATTERN = re.compile(r"(?i)^вирус\s+(\d+(?:[.,]\d+)?)\s*$")
_CURE_PATTERN = re.compile(r"(?i)^антивирус$")


def _matches_virus_attack(message: Message) -> bool:
    return bool(
        message.text
        and _ATTACK_PATTERN.match(message.text.strip())
        and message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
    )


def _matches_virus_cure(message: Message) -> bool:
    return bool(message.text and _CURE_PATTERN.match(message.text.strip()))


def _target_label(target: User) -> str:
    return f"@{target.username}" if target.username else escape(target.first_name or "игрок")


def _format_cooldown_remaining(attacker: User, now: datetime) -> str:
    remaining = COOLDOWN - (now - attacker.virus_last_used_at)
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    return f"⏳ Команду «Вирус» можно использовать раз в 24 часа. Осталось: {hours}ч {minutes}м."


async def _execute_attack(
    session: AsyncSession, attacker: User, target_id: int, stake: Decimal,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """One full infection attempt: validation, stake debit, and the 40%
    roll. Shared by the original "Вирус <ставка>" command and the ammo-
    bonus retry (cb_virus_buy_ammo) -- they differ only in how target_id/
    stake were obtained and how the result gets sent."""
    used_bonus_attempt = attacker.virus_bonus_attempt
    if used_bonus_attempt:
        # Consumed the instant this attempt starts, before any validation
        # below -- per its own contract ("consumed... whether or not that
        # use succeeds", see the User model column comment), a one-time
        # cooldown bypass must not survive an early rejection (self-
        # target/unregistered target/already-infected/insufficient
        # balance) or it becomes a perpetually-live token silently
        # defeating the 24h cooldown whenever it's eventually redeemed --
        # e.g. ammo succeeds, the immediate retry then hits "already
        # infected" because someone else got there first, or the retry's
        # own stake can no longer be afforded after paying for the ammo.
        attacker.virus_bonus_attempt = False
        await session.commit()

    if target_id == attacker.user_id:
        return "❌ Нельзя заразить самого себя.", None
    target = await session.get(User, target_id)
    if target is None:
        return "❌ Эта цель не зарегистрирована в боте.", None

    repo = VirusInfectionRepository(session)
    if await repo.get(target_id) is not None:
        return "❌ Этот игрок уже заражён.", None

    now = datetime.utcnow()
    if not used_bonus_attempt and attacker.virus_last_used_at is not None:
        if now - attacker.virus_last_used_at < COOLDOWN:
            return _format_cooldown_remaining(attacker, now), None

    if not await debit_stars_if_enough(session, attacker.user_id, stake):
        return f"❌ Недостаточно RP⭐️. Нужно: {stake:.2f} RP⭐️.", None

    # Checks passed and the stake is charged -- the attempt now counts
    # against the cooldown regardless of the roll below (the bonus flag,
    # if used, was already consumed above).
    attacker.virus_last_used_at = now
    await session.commit()

    target_label = _target_label(target)
    if not roll_infect_success():
        return f"❌ Не удалось заразить игрока {target_label}.", virus_ammo_kb(target_id, str(stake))

    virus_type = roll_virus_type()
    payout = infection_payout(stake, virus_type)
    await credit_stars(session, attacker.user_id, payout)
    created = await repo.create(target_id, attacker.user_id, virus_type)
    if created is None:
        # Lost a race against someone else infecting the same target in
        # the instant between our own check above and this insert --
        # nothing was actually created, so the payout must not stand.
        await credit_stars(session, attacker.user_id, -payout)
        return "❌ Этот игрок уже заражён.", None

    label = VIRUS_TYPES[virus_type].label
    return (
        f"🦠 <b>{target_label}</b> заражён вирусом! Тип: <b>{label}</b>.\n"
        f"💰 Выплата: <b>+{payout:.2f} RP⭐️</b>\n\n"
        f"Статус «Заражён» до выздоровления.",
        None,
    )


@router.message(_matches_virus_attack)
async def msg_virus_attack(message: Message, session: AsyncSession, chat: Chat | None = None) -> None:
    if message.text is None or message.from_user is None or message.reply_to_message is None:
        return
    if chat is not None and not chat.games_enabled:
        await message.reply("🎮 Игры отключены в этом чате.")
        return
    reply_from = message.reply_to_message.from_user
    if reply_from is None:
        return
    match = _ATTACK_PATTERN.match(message.text.strip())
    try:
        stake = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return
    if stake <= 0:
        await message.reply("❌ Ставка должна быть положительной.")
        return

    attacker = await session.get(User, message.from_user.id)
    if attacker is None:
        await message.reply(REGISTRATION_REQUIRED_TEXT, reply_markup=registration_required_kb(settings.bot_username))
        return

    text, kb = await _execute_attack(session, attacker, reply_from.id, stake)
    await message.reply(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("virus:ammo:"))
async def cb_virus_buy_ammo(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.from_user is None or callback.data is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        target_id = int(parts[2])
        stake = Decimal(parts[3])
    except (ValueError, InvalidOperation):
        await callback.answer()
        return

    attacker = await session.get(User, callback.from_user.id)
    if attacker is None:
        await callback.answer(REGISTRATION_REQUIRED_TEXT, show_alert=True)
        return

    if not await debit_stars_if_enough(session, attacker.user_id, AMMO_COST):
        await callback.answer(f"❌ Недостаточно RP⭐️. Нужно: {AMMO_COST:.0f} RP⭐️.", show_alert=True)
        return
    await callback.answer()

    if not roll_ammo_success():
        await callback.message.answer(
            f"💥 Боеприпас оказался бракованным. <b>{AMMO_COST:.0f} RP⭐️</b> сгорели.",
            parse_mode="HTML", reply_markup=virus_ammo_kb(target_id, str(stake)),
        )
        return

    attacker.virus_bonus_attempt = True
    await session.commit()

    text, kb = await _execute_attack(session, attacker, target_id, stake)
    await callback.message.answer(
        f"🧪 Боеприпас сработал! Дополнительная попытка заражения...\n\n{text}",
        parse_mode="HTML", reply_markup=kb,
    )


@router.message(_matches_virus_cure)
async def msg_virus_cure(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    infection = await VirusInfectionRepository(session).get(message.from_user.id)
    if infection is None:
        await message.reply("✅ Вы не заражены.")
        return
    await message.reply(
        "💉 <b>Лечение</b>\n\nВыберите способ:",
        parse_mode="HTML", reply_markup=virus_cure_kb(),
    )


async def _cure_attempt(callback: CallbackQuery, session: AsyncSession, cost: Decimal, guaranteed: bool) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    infection = await VirusInfectionRepository(session).get(callback.from_user.id)
    if infection is None:
        await callback.answer("✅ Вы не заражены.", show_alert=True)
        return
    if not await debit_stars_if_enough(session, callback.from_user.id, cost):
        await callback.answer(f"❌ Недостаточно RP⭐️. Нужно: {cost:.0f} RP⭐️.", show_alert=True)
        return
    await callback.answer()

    cured = guaranteed or roll_herbs_success()
    if not cured:
        await callback.message.answer(
            f"🌿 Травы не помогли. <b>{cost:.0f} RP⭐️</b> потрачены впустую. Статус «Заражён» сохраняется.",
            parse_mode="HTML",
        )
        return

    await VirusInfectionRepository(session).cure(callback.from_user.id)
    await callback.message.answer("✅ Вы вылечились! Статус «Заражён» снят.")


@router.callback_query(F.data == "virus:cure:medicine")
async def cb_virus_cure_medicine(callback: CallbackQuery, session: AsyncSession) -> None:
    await _cure_attempt(callback, session, MEDICINE_COST, guaranteed=True)


@router.callback_query(F.data == "virus:cure:herbs")
async def cb_virus_cure_herbs(callback: CallbackQuery, session: AsyncSession) -> None:
    await _cure_attempt(callback, session, HERBS_COST, guaranteed=False)
