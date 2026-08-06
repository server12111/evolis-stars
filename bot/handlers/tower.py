import math
import random

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, GameSession
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.random_game import try_consume_free_game_credit
from bot.keyboards.tower import tower_bet_kb, tower_playing_kb, tower_over_kb, tower_cancel_kb
from bot.services.house_edge import is_in_recovery
from bot.states.games import TowerStates

router = Router()


async def _get_coeff(session: AsyncSession, level: int) -> float:
    repo = SettingsRepository(session)
    return await repo.get_float(f"tower_coeff_{level}", [1.05, 1.20, 1.40, 1.65, 1.95, 2.30, 2.70, 3.20][min(level, 7)])


@router.callback_query(lambda c: c.data == "menu:tower")
async def cb_tower_menu(callback: CallbackQuery, db_user: User, state: FSMContext, session: AsyncSession) -> None:
    current = await state.get_state()
    if current == TowerStates.playing:
        await callback.answer("⚠️ У вас уже есть активная игра! Завершите её.", show_alert=True)
        return

    await state.clear()
    s_repo = SettingsRepository(session)
    if not await s_repo.get_bool("tower_enabled", True):
        await callback.answer("🗼 Башня временно недоступна.", show_alert=True)
        return

    min_bet = await s_repo.get_float("tower_min_bet", 1.0)
    text = (
        f"🗼 <b>Башня</b>\n\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>\n"
        f"Мин. ставка: <b>{min_bet:.0f} RP⭐️</b>\n\n"
        "8 уровней, 3 плитки/уровень, 1 мина/уровень.\n"
        "Выбери плитку без мины и поднимайся выше!\n\n"
        "Выбери ставку:"
    )
    await state.set_state(TowerStates.choose_bet)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=tower_bet_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=tower_bet_kb())
    await callback.answer()


@router.callback_query(TowerStates.choose_bet, lambda c: c.data and c.data.startswith("tower:bet:") and c.data != "tower:bet:custom")
async def cb_tower_bet(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    try:
        bet = float(callback.data.split(":")[2])
        if not math.isfinite(bet) or bet < 1:
            raise ValueError
    except (IndexError, ValueError):
        await callback.answer("❌ Неверная ставка.", show_alert=True)
        return
    await _start_tower(callback, None, state, session, db_user, bet)


@router.callback_query(TowerStates.choose_bet, lambda c: c.data == "tower:bet:custom")
async def cb_tower_custom(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        await callback.message.edit_text("🗼 Введи сумму ставки:", reply_markup=tower_cancel_kb())
    except Exception:
        await callback.message.answer("🗼 Введи сумму ставки:", reply_markup=tower_cancel_kb())
    await callback.answer()


@router.message(TowerStates.choose_bet)
async def msg_tower_bet(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    try:
        bet = float(message.text.strip().replace(",", "."))
        if not math.isfinite(bet) or bet <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:", reply_markup=tower_cancel_kb())
        return
    await _start_tower(None, message, state, session, db_user, bet)


async def _start_tower(callback, message, state: FSMContext, session: AsyncSession, db_user: User, bet: float) -> None:
    s_repo = SettingsRepository(session)
    min_bet = await s_repo.get_float("tower_min_bet", 1.0)
    max_levels = await s_repo.get_int("tower_levels", 8)

    async def _reply(text, kb):
        if callback:
            try:
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

    if bet < min_bet:
        txt = f"❌ Мин. ставка: <b>{min_bet:.0f} RP⭐️</b>"
        await _reply(txt, tower_cancel_kb())
        if callback:
            await callback.answer()
        return

    max_bet = await s_repo.get_float("tower_max_bet", 500.0)
    if bet > max_bet:
        txt = f"❌ Макс. ставка: <b>{max_bet:.0f} RP⭐️</b>"
        await _reply(txt, tower_cancel_kb())
        if callback:
            await callback.answer()
        return

    free = await try_consume_free_game_credit(session, db_user, bet)
    if not free and float(db_user.stars_balance) < bet:
        await _reply("❌ Недостаточно звёзд.", tower_cancel_kb())
        if callback:
            await callback.answer()
        return

    # Deduct bet (unless it was covered by a free-game credit)
    if not free:
        db_user.stars_balance = round(float(db_user.stars_balance) - bet, 2)
    await session.commit()

    # Recovery mode: 2-of-3 tiles are mines instead of 1-of-3, frozen for the
    # whole round at start so odds can't shift mid-game. See house_edge.py.
    mine_count = 2 if await is_in_recovery(session, "tower") else 1
    mines = [random.sample(range(3), mine_count) for _ in range(max_levels)]
    await state.set_state(TowerStates.playing)
    await state.update_data(bet=bet, level=0, max_levels=max_levels, mines=mines, history=[], used_free_credit=free)

    coeff = await _get_coeff(session, 0)
    payout = round(bet * coeff, 2)

    text = (
        f"🗼 <b>Башня</b>\n\n"
        f"Ставка: <b>{bet:.2f} RP⭐️</b> | Уровень: <b>1/{max_levels}</b>\n"
        f"Коэф: <b>×{coeff:.2f}</b> | Возможный выигрыш: <b>{payout:.2f} RP⭐️</b>\n\n"
        "Выбери плитку 🟩:"
    )
    kb = tower_playing_kb(0, max_levels, mines, [], coeff, payout, bet)
    await _reply(text, kb)
    if callback:
        await callback.answer()


@router.callback_query(TowerStates.playing, lambda c: c.data and c.data.startswith("tower:pick:"))
async def cb_tower_pick(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    try:
        slot = int(callback.data.split(":")[2])
        if slot not in (0, 1, 2):
            raise ValueError
    except (IndexError, ValueError):
        await callback.answer()
        return

    data = await state.get_data()
    level = data["level"]
    max_levels = data["max_levels"]
    mines = data["mines"]
    history = data["history"]
    bet = data["bet"]

    mine_positions = mines[level]
    history = history + [slot]

    if slot in mine_positions:
        # Hit a mine — game over
        session.add(GameSession(
            user_id=db_user.user_id, game_type="tower",
            bet=bet, payout=0, result="lose",
        ))
        s_repo = SettingsRepository(session)
        await s_repo.add_float("tower_total_bet", bet)
        await s_repo.add_float("tower_total_payout", 0)
        await session.commit()
        await state.clear()

        text = (
            f"💣 <b>Мина! Башня рухнула.</b>\n\n"
            f"Ставка: <b>{bet:.2f} RP⭐️</b>\n"
            f"Уровень: <b>{level + 1}/{max_levels}</b>\n"
            f"Потеря: <b>-{bet:.2f} RP⭐️</b>\n"
            f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>"
        )
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=tower_over_kb(False))
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=tower_over_kb(False))
    else:
        new_level = level + 1
        if new_level >= max_levels:
            # Reached the top — full win
            coeff = await _get_coeff(session, max_levels - 1)
            payout = round(bet * coeff, 2)
            db_user.stars_balance = round(float(db_user.stars_balance) + payout, 2)
            session.add(GameSession(
                user_id=db_user.user_id, game_type="tower",
                bet=bet, payout=payout, result="win",
            ))
            s_repo = SettingsRepository(session)
            await s_repo.add_float("tower_total_bet", bet)
            await s_repo.add_float("tower_total_payout", payout)
            await session.commit()
            await state.clear()

            text = (
                f"🎉 <b>Башня покорена!</b>\n\n"
                f"Ставка: <b>{bet:.2f} RP⭐️</b>\n"
                f"Уровень: <b>{max_levels}/{max_levels}</b>\n"
                f"Коэф: <b>×{coeff:.2f}</b>\n"
                f"Выигрыш: <b>+{payout:.2f} RP⭐️</b>\n"
                f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>"
            )
            try:
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=tower_over_kb(True))
            except Exception:
                await callback.message.answer(text, parse_mode="HTML", reply_markup=tower_over_kb(True))
        else:
            await state.update_data(level=new_level, history=history)
            # Must match what tower:cashout actually pays for this exact
            # state (_get_coeff(level - 1) there, once `level` becomes
            # new_level) — using new_level here instead of the pre-increment
            # level would show/promise one tier higher than cashout pays.
            coeff = await _get_coeff(session, level)
            payout = round(bet * coeff, 2)

            text = (
                f"🗼 <b>Башня</b>\n\n"
                f"Ставка: <b>{bet:.2f} RP⭐️</b> | Уровень: <b>{new_level + 1}/{max_levels}</b>\n"
                f"Коэф: <b>×{coeff:.2f}</b> | Возможный выигрыш: <b>{payout:.2f} RP⭐️</b>\n\n"
                "Выбери плитку 🟩:"
            )
            kb = tower_playing_kb(new_level, max_levels, mines, history, coeff, payout, bet)
            try:
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    await callback.answer()


@router.callback_query(TowerStates.playing, lambda c: c.data == "tower:cashout")
async def cb_tower_cashout(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    data = await state.get_data()
    bet = data["bet"]
    level = data["level"]
    max_levels = data["max_levels"]
    if level == 0:
        await callback.answer("⚠️ Сначала пройди хотя бы один уровень!", show_alert=True)
        return

    coeff = await _get_coeff(session, level - 1)
    payout = round(bet * coeff, 2)

    db_user.stars_balance = round(float(db_user.stars_balance) + payout, 2)
    session.add(GameSession(
        user_id=db_user.user_id, game_type="tower",
        bet=bet, payout=payout, result="win",
    ))
    s_repo = SettingsRepository(session)
    await s_repo.add_float("tower_total_bet", bet)
    await s_repo.add_float("tower_total_payout", payout)
    await session.commit()
    await state.clear()

    net = round(payout - bet, 2)
    text = (
        f"💰 <b>Выигрыш забран!</b>\n\n"
        f"Ставка: <b>{bet:.2f} RP⭐️</b>\n"
        f"Пройдено уровней: <b>{level}/{max_levels}</b>\n"
        f"Коэф: <b>×{coeff:.2f}</b>\n"
        f"Выигрыш: <b>+{payout:.2f} RP⭐️</b> (прибыль: +{net:.2f} RP⭐️)\n"
        f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=tower_over_kb(True))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=tower_over_kb(True))
    await callback.answer()


@router.callback_query(TowerStates.playing, lambda c: c.data == "tower:quit")
async def cb_tower_quit(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    data = await state.get_data()
    bet = data.get("bet", 0)
    level = data.get("level", 0)

    if level == 0:
        # No progress — refund, but only if the bet actually left the real
        # balance in the first place. A bet covered by a free-game credit
        # never touched stars_balance, so "refunding" it here would have
        # minted stars from nothing.
        if not data.get("used_free_credit", False):
            db_user.stars_balance = round(float(db_user.stars_balance) + bet, 2)
            await session.commit()
        await state.clear()
        await callback.answer("↩️ Ставка возвращена.")
    else:
        await callback.answer("⚠️ Сначала заберите выигрыш!", show_alert=True)
        return

    from bot.database.repositories.chat import ChatRepository
    from bot.keyboards.main import main_menu_kb
    kb = main_menu_kb(await ChatRepository(session).has_owned_chats(db_user.user_id))
    try:
        await callback.message.edit_text(
            f"🏠 <b>Главное меню</b>\n\n💰 Баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>",
            parse_mode="HTML", reply_markup=kb,
        )
    except Exception:
        await callback.message.answer("🏠 <b>Главное меню</b>", parse_mode="HTML", reply_markup=kb)


@router.callback_query(lambda c: c.data == "tower:noop")
async def cb_tower_noop(callback: CallbackQuery) -> None:
    await callback.answer()
