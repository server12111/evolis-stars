import asyncio
import math
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.engine import SessionFactory
from bot.database.models import Duel, User
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.duel import (
    active_duels_kb,
    back_to_duel_kb,
    duel_confirm_kb,
    duel_creator_kb,
    duel_menu_kb,
    duel_roll_kb,
    duel_view_kb,
)
from bot.services.background import spawn_background
from bot.services.duel_scheduler import compute_duel_payout
from bot.services.referral import referrals_word
from bot.states.duel import DuelStates

router = Router()

DUEL_EXPIRE_MINUTES = 15
CONFIRM_TIMEOUT_MINUTES = 5
DICE_TIMEOUT_MINUTES = 10

async def _duel_rules(session: AsyncSession) -> tuple[int, float]:
    repo = SettingsRepository(session)
    min_refs = max(0, await repo.get_int("duel_min_refs", 3))
    commission_percent = await repo.get_float("duel_commission", 20.0)
    commission = min(1.0, max(0.0, commission_percent / 100))
    return min_refs, commission


async def _notify(bot: Bot, user_id: int, text: str, kb=None) -> None:
    try:
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


async def _delayed_resolve(duel_id: int, bot: Bot) -> None:
    await asyncio.sleep(4)
    async with SessionFactory() as session:
        duel = await session.get(Duel, duel_id)
        if (
            duel
            and duel.status == "active"
            and duel.creator_roll is not None
            and duel.joiner_roll is not None
        ):
            await _resolve_duel(duel, session, bot)


async def _resolve_duel(duel: Duel, session: AsyncSession, bot: Bot) -> None:
    if duel.status != "active":
        return
    claim = await session.execute(
        update(Duel)
        .where(Duel.id == duel.id, Duel.status == "active")
        .values(status="settling")
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        await session.rollback()
        return
    duel.status = "settling"
    c_roll, j_roll = duel.creator_roll, duel.joiner_roll
    commission_percent = await SettingsRepository(session).get_float("duel_commission", 20.0)
    winner_amount = compute_duel_payout(Decimal(duel.amount), commission_percent)
    duel.status = "finished"

    if c_roll == j_roll:
        creator = await session.get(User, duel.creator_id)
        joiner = await session.get(User, duel.joiner_id)
        if creator:
            await session.execute(
                update(User).where(User.user_id == creator.user_id).values(stars_balance=User.stars_balance + duel.amount)
            )
        if joiner:
            await session.execute(
                update(User).where(User.user_id == joiner.user_id).values(stars_balance=User.stars_balance + duel.amount)
            )
        duel.winner_id = None
        await session.commit()
        txt = f"🤝 <b>Дуэль #{duel.id} — Ничья!</b>\n\n🎲 {c_roll} vs {j_roll}\n💫 Ставки возвращены."
        await _notify(bot, duel.creator_id, txt, back_to_duel_kb())
        await _notify(bot, duel.joiner_id, txt, back_to_duel_kb())
        return

    winner_id = duel.creator_id if c_roll > j_roll else duel.joiner_id
    winner = await session.get(User, winner_id)
    winner_name = escape(winner.first_name) if winner else "Игрок"
    if winner:
        await session.execute(
            update(User).where(User.user_id == winner.user_id).values(stars_balance=User.stars_balance + winner_amount)
        )
    duel.winner_id = winner_id
    await session.commit()

    txt = (f"🏆 <b>Дуэль #{duel.id} завершена!</b>\n\n🎲 {c_roll} vs {j_roll}\n"
           f"🥇 Победитель: <b>{winner_name}</b>\n💰 Выигрыш: <b>{winner_amount:.2f} RP⭐️</b>")
    await _notify(bot, duel.creator_id, txt, back_to_duel_kb())
    await _notify(bot, duel.joiner_id, txt, back_to_duel_kb())


@router.callback_query(lambda c: c.data == "duel:menu")
async def cb_duel_menu(
    callback: CallbackQuery,
    db_user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await state.clear()
    min_refs, commission = await _duel_rules(session)
    if db_user.referrals_count < min_refs:
        await callback.answer(f"❌ Нужно минимум {min_refs} {referrals_word(min_refs)}. У тебя: {db_user.referrals_count}", show_alert=True)
        return
    text = (f"⚔️ <b>Дуэли</b>\n\n💰 Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>\n\n"
            f"Бросайте кубик против соперника!\nПобедитель получает <b>{(1 - commission) * 100:.0f}%</b> банка.")
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=duel_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=duel_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "duel:create")
async def cb_duel_create(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
    session: AsyncSession,
) -> None:
    min_refs, _ = await _duel_rules(session)
    if db_user.referrals_count < min_refs:
        await callback.answer(
            f"❌ Нужно минимум {min_refs} {referrals_word(min_refs)}.",
            show_alert=True,
        )
        return
    await state.set_state(DuelStates.enter_amount)
    try:
        await callback.message.edit_text(
            f"⚔️ <b>Создать дуэль</b>\n\n💰 Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>\n\nВведи сумму ставки:",
            parse_mode="HTML", reply_markup=back_to_duel_kb(),
        )
    except Exception:
        await callback.message.answer(
            "⚔️ Введи сумму ставки:", reply_markup=back_to_duel_kb(),
        )
    await callback.answer()


@router.message(DuelStates.enter_amount)
async def msg_duel_amount(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    min_refs, _ = await _duel_rules(session)
    if db_user.referrals_count < min_refs:
        await state.clear()
        await message.answer(f"❌ Нужно минимум {min_refs} {referrals_word(min_refs)}.")
        return
    try:
        amount = float(message.text.strip().replace(",", "."))
        if not math.isfinite(amount) or amount <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:")
        return
    if db_user.stars_balance < amount:
        await message.answer(f"❌ Недостаточно звёзд. Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>", parse_mode="HTML")
        return

    await state.clear()
    db_user.stars_balance = round(float(db_user.stars_balance) - amount, 2)
    expires_at = datetime.utcnow() + timedelta(minutes=DUEL_EXPIRE_MINUTES)
    duel = Duel(creator_id=db_user.user_id, amount=amount, expires_at=expires_at)
    session.add(duel)
    await session.flush()
    await session.commit()

    await message.answer(
        f"⚔️ <b>Дуэль #{duel.id} создана!</b>\n\n💰 Ставка: <b>{amount:.0f} RP⭐️</b>\n"
        f"⏳ Ожидание соперника... Если никто не вступит за {DUEL_EXPIRE_MINUTES} мин, "
        "ставка вернётся автоматически.",
        parse_mode="HTML", reply_markup=duel_creator_kb(duel.id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("duel:cancel:"))
async def cb_duel_cancel(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    duel_id = int(callback.data.split(":")[2])
    duel = await session.get(Duel, duel_id)
    if not duel or duel.status != "waiting":
        await callback.answer("❌ Нельзя отменить.", show_alert=True)
        return
    if duel.creator_id != db_user.user_id:
        await callback.answer("❌ Не ваша дуэль.", show_alert=True)
        return
    claim = await session.execute(
        update(Duel)
        .where(Duel.id == duel_id, Duel.status == "waiting")
        .values(status="cancelled")
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        await session.rollback()
        await callback.answer("❌ Нельзя отменить.", show_alert=True)
        return
    db_user.stars_balance += duel.amount
    duel.status = "cancelled"
    await session.commit()
    try:
        await callback.message.edit_text(
            f"❌ <b>Дуэль #{duel_id} отменена.</b>\n\n💫 <b>{float(duel.amount):.0f} RP⭐️</b> возвращено.",
            parse_mode="HTML", reply_markup=back_to_duel_kb(),
        )
    except Exception:
        await callback.message.answer(f"❌ Дуэль #{duel_id} отменена.", reply_markup=back_to_duel_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "duel:active")
async def cb_duel_active(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    min_refs, _ = await _duel_rules(session)
    if db_user.referrals_count < min_refs:
        await callback.answer(
            f"❌ Нужно минимум {min_refs} {referrals_word(min_refs)}.",
            show_alert=True,
        )
        return
    now = datetime.utcnow()
    duels = (await session.execute(
        select(Duel).where(Duel.status == "waiting", Duel.creator_id != db_user.user_id, Duel.expires_at > now)
        .order_by(Duel.created_at.desc())
    )).scalars().all()
    if not duels:
        text = "⚔️ <b>Активные дуэли</b>\n\n😔 Нет доступных дуэлей. Создай свою!"
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_duel_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=back_to_duel_kb())
    else:
        text = f"⚔️ <b>Активные дуэли</b> — {len(duels)} шт."
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=active_duels_kb(duels))
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=active_duels_kb(duels))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("duel:view:"))
async def cb_duel_view(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    min_refs, commission = await _duel_rules(session)
    if db_user.referrals_count < min_refs:
        await callback.answer(
            f"❌ Нужно минимум {min_refs} {referrals_word(min_refs)}.",
            show_alert=True,
        )
        return
    duel_id = int(callback.data.split(":")[2])
    duel = await session.get(Duel, duel_id)
    if not duel or duel.status != "waiting" or duel.expires_at < datetime.utcnow():
        await callback.answer("❌ Дуэль недоступна.", show_alert=True)
        return
    if duel.creator_id == db_user.user_id:
        await callback.answer("❌ Нельзя вступить в свою дуэль.", show_alert=True)
        return
    creator = await session.get(User, duel.creator_id)
    mins_left = max(0, int((duel.expires_at - datetime.utcnow()).total_seconds() // 60))
    try:
        await callback.message.edit_text(
            f"⚔️ <b>Дуэль #{duel.id}</b>\n\n👤 Создатель: <b>{escape(creator.first_name) if creator else 'Игрок'}</b>\n"
            f"💰 Ставка: <b>{float(duel.amount):.0f} RP⭐️</b>\n⏳ Истекает: <b>{mins_left} мин</b>\n\n"
            f"Победитель получит <b>{float(duel.amount) * 2 * (1 - commission):.0f} RP⭐️</b>",
            parse_mode="HTML", reply_markup=duel_view_kb(duel.id),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("duel:join:"))
async def cb_duel_join(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    min_refs, _ = await _duel_rules(session)
    if db_user.referrals_count < min_refs:
        await callback.answer(
            f"❌ Нужно минимум {min_refs} {referrals_word(min_refs)}.",
            show_alert=True,
        )
        return
    duel_id = int(callback.data.split(":")[2])
    duel = await session.get(Duel, duel_id)
    if not duel or duel.status != "waiting" or duel.expires_at < datetime.utcnow():
        await callback.answer("❌ Дуэль недоступна.", show_alert=True)
        return
    if duel.creator_id == db_user.user_id:
        await callback.answer("❌ Нельзя вступить в свою дуэль.", show_alert=True)
        return
    if db_user.stars_balance < duel.amount:
        await callback.answer(f"❌ Нужно {float(duel.amount):.0f} RP⭐️", show_alert=True)
        return

    expires_at = datetime.utcnow() + timedelta(minutes=CONFIRM_TIMEOUT_MINUTES)
    claim = await session.execute(
        update(Duel)
        .where(
            Duel.id == duel_id,
            Duel.status == "waiting",
            Duel.expires_at > datetime.utcnow(),
        )
        .values(
            joiner_id=db_user.user_id,
            status="confirming",
            expires_at=expires_at,
        )
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        await session.rollback()
        await callback.answer("❌ Дуэль уже занята.", show_alert=True)
        return

    db_user.stars_balance -= duel.amount
    duel.joiner_id = db_user.user_id
    duel.status = "confirming"
    duel.expires_at = expires_at
    await session.commit()

    creator = await session.get(User, duel.creator_id)
    await _notify(callback.bot, duel.creator_id,
                  f"⚔️ <b>Дуэль #{duel_id}</b>\n\n👤 <b>{escape(db_user.first_name)}</b> хочет вступить!\n"
                  f"💰 Ставка: <b>{float(duel.amount):.0f} RP⭐️</b>\n\n"
                  f"Подтвердить? (не ответишь за {CONFIRM_TIMEOUT_MINUTES} мин — ставки вернутся обоим)",
                  duel_confirm_kb(duel_id))
    try:
        await callback.message.edit_text(
            f"⏳ Ожидание подтверждения от <b>{escape(creator.first_name) if creator else 'создателя'}</b>...",
            parse_mode="HTML", reply_markup=back_to_duel_kb(),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("duel:confirm:"))
async def cb_duel_confirm_join(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    duel_id = int(callback.data.split(":")[2])
    duel = await session.get(Duel, duel_id)
    if not duel or duel.status != "confirming" or duel.creator_id != db_user.user_id:
        await callback.answer("❌ Недоступно.", show_alert=True)
        return
    expires_at = datetime.utcnow() + timedelta(minutes=DICE_TIMEOUT_MINUTES)
    claim = await session.execute(
        update(Duel)
        .where(
            Duel.id == duel_id,
            Duel.status == "confirming",
            Duel.creator_id == db_user.user_id,
            Duel.expires_at > datetime.utcnow(),
        )
        .values(status="active", expires_at=expires_at)
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        await session.rollback()
        await callback.answer("❌ Время подтверждения истекло.", show_alert=True)
        return
    duel.status = "active"
    duel.expires_at = expires_at
    await session.commit()
    roll_kb = duel_roll_kb(duel_id)
    joiner = await session.get(User, duel.joiner_id)
    try:
        await callback.message.edit_text(
            f"🔥 <b>Дуэль #{duel_id} началась!</b>\n\n⚔️ Соперник: <b>{escape(joiner.first_name) if joiner else 'Игрок'}</b>\n"
            f"💰 Ставка: <b>{float(duel.amount):.0f} RP⭐️</b>\n\n🎲 Бросьте кубик! (не успеете за {DICE_TIMEOUT_MINUTES} мин — ставка сгорит)",
            parse_mode="HTML", reply_markup=roll_kb,
        )
    except Exception:
        pass
    await callback.answer()
    await _notify(callback.bot, duel.joiner_id,
                  f"🔥 <b>Дуэль #{duel_id} подтверждена!</b>\n\n⚔️ Соперник: <b>{escape(db_user.first_name)}</b>\n"
                  f"💰 Ставка: <b>{float(duel.amount):.0f} RP⭐️</b>\n\n🎲 Бросьте кубик! (не успеете за {DICE_TIMEOUT_MINUTES} мин — ставка сгорит)", roll_kb)


@router.callback_query(lambda c: c.data and c.data.startswith("duel:decline_join:"))
async def cb_duel_decline_join(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    duel_id = int(callback.data.split(":")[2])
    duel = await session.get(Duel, duel_id)
    if not duel or duel.status != "confirming" or duel.creator_id != db_user.user_id:
        await callback.answer("❌ Недоступно.", show_alert=True)
        return
    claim = await session.execute(
        update(Duel)
        .where(
            Duel.id == duel_id,
            Duel.status == "confirming",
            Duel.creator_id == db_user.user_id,
        )
        .values(status="settling")
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        await session.rollback()
        await callback.answer("❌ Дуэль уже обработана.", show_alert=True)
        return
    joiner_id = duel.joiner_id
    participant_ids = [duel.creator_id]
    if joiner_id is not None:
        participant_ids.append(joiner_id)
    await session.execute(
        update(User)
        .where(User.user_id.in_(participant_ids))
        .values(stars_balance=User.stars_balance + duel.amount)
        .execution_options(synchronize_session=False)
    )
    duel.status = "cancelled"
    await session.commit()
    try:
        await callback.message.edit_text(f"❌ Дуэль #{duel_id} отклонена. Ставка возвращена.", reply_markup=back_to_duel_kb())
    except Exception:
        pass
    await callback.answer()
    await _notify(callback.bot, joiner_id, f"❌ Дуэль #{duel_id} отклонена создателем. Ставка возвращена.", back_to_duel_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("duel:roll:"))
async def cb_duel_roll(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    duel_id = int(callback.data.split(":")[2])
    duel = await session.get(Duel, duel_id)
    if not duel or duel.status != "active":
        await callback.answer("❌ Дуэль уже завершена.", show_alert=True)
        return
    is_creator = duel.creator_id == db_user.user_id
    is_joiner = duel.joiner_id == db_user.user_id
    if not (is_creator or is_joiner):
        await callback.answer("❌ Вы не участник.", show_alert=True)
        return
    if is_creator and duel.creator_roll is not None:
        await callback.answer("🎲 Вы уже бросили кубик.", show_alert=True)
        return
    if is_joiner and duel.joiner_roll is not None:
        await callback.answer("🎲 Вы уже бросили кубик.", show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()

    dice_msg = await callback.bot.send_dice(chat_id=callback.message.chat.id, emoji="🎲")
    value = dice_msg.dice.value

    if is_creator:
        duel.creator_roll = value
    else:
        duel.joiner_roll = value
    await session.commit()
    # expire_on_commit=False means `duel` still reflects only this request's
    # own write; refresh so a near-simultaneous roll from the other
    # participant (committed just before/after this one) is actually seen
    # before deciding whether both sides have rolled.
    await session.refresh(duel)

    if duel.creator_roll is not None and duel.joiner_roll is not None:
        spawn_background(
            _delayed_resolve(duel_id, callback.bot),
            name=f"resolve-duel-{duel_id}",
        )
    else:
        other_id = duel.joiner_id if is_creator else duel.creator_id
        await _notify(callback.bot, other_id,
                      f"⚔️ <b>Дуэль #{duel_id}</b>\n\n🎲 Соперник бросил кубик: <b>{value}</b>\nВаша очередь!",
                      duel_roll_kb(duel_id))


@router.callback_query(lambda c: c.data == "duel:history")
async def cb_duel_history(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    duels = (await session.execute(
        select(Duel).where(Duel.status == "finished").order_by(Duel.created_at.desc()).limit(20)
    )).scalars().all()

    if not duels:
        try:
            await callback.message.edit_text("📜 <b>История дуэлей</b>\n\nНет завершённых дуэлей.", parse_mode="HTML", reply_markup=back_to_duel_kb())
        except Exception:
            await callback.message.answer("📜 <b>История дуэлей</b>\n\nНет завершённых дуэлей.", parse_mode="HTML", reply_markup=back_to_duel_kb())
        await callback.answer()
        return

    lines = []
    for d in duels[:10]:
        creator = await session.get(User, d.creator_id)
        joiner = await session.get(User, d.joiner_id) if d.joiner_id else None
        c_name = escape(f"@{creator.username}" if creator and creator.username else (creator.first_name if creator else "?"))
        j_name = escape(f"@{joiner.username}" if joiner and joiner.username else (joiner.first_name if joiner else "?"))
        if d.winner_id is None:
            result = "🤝 Ничья"
        else:
            winner = await session.get(User, d.winner_id)
            result = f"🏆 {escape(winner.first_name) if winner else '?'}"
        lines.append(f"⚔️ #{d.id} | {c_name} vs {j_name} | {float(d.amount):.0f}RP⭐️ | {result}")

    text = "📜 <b>История дуэлей</b>\n\n" + "\n".join(lines)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_duel_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=back_to_duel_kb())
    await callback.answer()
