import math

from aiogram import Router, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, GameSession
from bot.database.repositories.content import DEFAULT_TEXTS, ContentRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.game import GameRepository
from bot.keyboards.main import back_to_menu_kb
from bot.services.referral import referrals_word
from bot.keyboards.games import (
    games_menu_kb, dice_side_kb, football_side_kb, bowling_side_kb,
    basketball_side_kb, darts_side_kb, game_result_kb, game_cancel_kb,
    GAME_TYPES, GAME_LABELS,
)
from bot.states.games import GameStates

router = Router()

GAME_EMOJIS = {
    "football": "⚽", "basketball": "🏀", "bowling": "🎳",
    "dice": "🎲", "slots": "🎰", "darts": "🎯",
}


async def _load_games_config(session: AsyncSession) -> dict:
    repo = SettingsRepository(session)
    configs = {}
    for game in GAME_TYPES:
        enabled = await repo.get_bool(f"game_{game}_enabled", True)
        min_bet = await repo.get_float(f"game_{game}_min_bet", 1.0)
        cfg = {"enabled": enabled, "min_bet": min_bet}
        if game == "slots":
            c1 = await repo.get_float("game_slots_coeff1", 42.2)
            c2 = await repo.get_float("game_slots_coeff2", 3.0)
            cfg["coeff_label"] = f"x{c2:.4g}–x{c1:.4g}"
        elif game == "football":
            cg = await repo.get_float("game_football_coeff_goal", 2.0)
            cm = await repo.get_float("game_football_coeff_miss", 1.33)
            cfg["coeff_label"] = f"Гол x{cg:.4g}/Промах x{cm:.4g}"
        elif game == "basketball":
            lo = await repo.get_float("game_basketball_coeff_miss", 2.0)
            hi = await repo.get_float("game_basketball_coeff_clean", 4.0)
            cfg["coeff_label"] = f"x{lo:.4g}–x{hi:.4g}"
        elif game == "darts":
            hi = await repo.get_float("game_darts_coeff_bullseye", 4.8)
            cfg["coeff_label"] = f"x{hi:.4g}"
        elif game == "bowling":
            cs = await repo.get_float("game_bowling_coeff_strike", 4.8)
            cp = await repo.get_float("game_bowling_coeff_partial", 1.2)
            cfg["coeff_label"] = f"x{cp:.4g}–x{cs:.4g}"
        else:
            c = await repo.get_float(f"game_{game}_coeff", 1.6)
            cfg["coeff_label"] = f"x{c:.4g}"
        configs[game] = cfg
    return configs


async def _execute_game(
    bot: Bot, chat_id: int, session: AsyncSession,
    db_user: User, game_type: str, bet: float, game_side: str | None = None,
) -> tuple[bool, float, int]:
    dice_msg = await bot.send_dice(chat_id=chat_id, emoji=GAME_EMOJIS[game_type])
    value = dice_msg.dice.value
    repo = SettingsRepository(session)

    won, payout = False, 0.0

    if game_type == "football":
        cg = await repo.get_float("game_football_coeff_goal", 2.0)
        cm = await repo.get_float("game_football_coeff_miss", 1.33)
        if value in (4, 5) and game_side == "goal":
            won, payout = True, round(bet * cg, 2)
        elif value not in (4, 5) and game_side == "miss":
            won, payout = True, round(bet * cm, 2)

    elif game_type == "basketball":
        c_clean = await repo.get_float("game_basketball_coeff_clean", 4.0)
        c_any = await repo.get_float("game_basketball_coeff_any", 2.0)
        c_stuck = await repo.get_float("game_basketball_coeff_stuck", 4.0)
        c_miss = await repo.get_float("game_basketball_coeff_miss", 2.0)
        if value == 5:
            if game_side == "clean": won, payout = True, round(bet * c_clean, 2)
            elif game_side == "any": won, payout = True, round(bet * c_any, 2)
        elif value == 4 and game_side == "any":
            won, payout = True, round(bet * c_any, 2)
        elif value == 3 and game_side == "stuck":
            won, payout = True, round(bet * c_stuck, 2)
        elif value in (1, 2) and game_side == "miss":
            won, payout = True, round(bet * c_miss, 2)

    elif game_type == "bowling":
        cs = await repo.get_float("game_bowling_coeff_strike", 4.8)
        cp = await repo.get_float("game_bowling_coeff_partial", 1.2)
        cm = await repo.get_float("game_bowling_coeff_miss", 4.8)
        if value == 6 and game_side == "strike":
            won, payout = True, round(bet * cs, 2)
        elif value in (2, 3, 4, 5) and game_side == "partial":
            won, payout = True, round(bet * cp, 2)
        elif value == 1 and game_side == "miss":
            won, payout = True, round(bet * cm, 2)

    elif game_type == "dice":
        coeff = await repo.get_float("game_dice_coeff", 1.6)
        if (game_side == "high" and value > 3) or (game_side == "low" and value < 4):
            won, payout = True, round(bet * coeff, 2)

    elif game_type == "slots":
        c777 = await repo.get_float("game_slots_coeff1", 42.2)
        cfruits = await repo.get_float("game_slots_coeff2", 3.0)
        if value == 64:
            won, payout = True, round(bet * c777, 2)
            db_user.slots_777_count = (db_user.slots_777_count or 0) + 1
        elif value in {1, 22, 43}:
            won, payout = True, round(bet * cfruits, 2)

    elif game_type == "darts":
        c_bull = await repo.get_float("game_darts_coeff_bullseye", 4.8)
        c_bounce = await repo.get_float("game_darts_coeff_bounce", 4.8)
        if value == 6 and game_side == "center":
            won, payout = True, round(bet * c_bull, 2)
            db_user.darts_bullseye_count = (db_user.darts_bullseye_count or 0) + 1
        elif value == 1 and game_side == "bounce":
            won, payout = True, round(bet * c_bounce, 2)

    if won:
        db_user.stars_balance = round(float(db_user.stars_balance) + payout, 2)

    session.add(GameSession(
        user_id=db_user.user_id, game_type=game_type,
        bet=bet, result="win" if won else "lose", payout=payout,
    ))
    await session.commit()
    return won, payout, value


def _result_text(game_type, won, bet, payout, value, balance, side=None) -> str:
    label = GAME_LABELS[game_type]
    net = round(payout - bet, 2)
    sign = "+" if net >= 0 else ""

    if game_type == "football":
        outcome = "⚽ Гол!" if value in (4, 5) else "🥅 Промах."
        result_line = (f"🎉 <b>Угадал! +{payout:.2f} ⭐</b> (прибыль: {sign}{net:.2f} ⭐)" if won
                       else f"😞 <b>Не угадал. -{bet:.2f} ⭐</b>")
    elif game_type == "basketball":
        outcomes = {5: "🏀 Чистый гол!", 4: "🏀 Гол!", 3: "😬 Застрял мяч...", 1: "🏀 Промах.", 2: "🏀 Промах."}
        outcome = outcomes.get(value, "")
        result_line = (f"🎉 <b>Угадал! +{payout:.2f} ⭐</b> (прибыль: {sign}{net:.2f} ⭐)" if won
                       else f"😞 <b>Не угадал. -{bet:.2f} ⭐</b>")
    elif game_type == "bowling":
        if value == 6: outcome = "🎳 Страйк!"
        elif value in (2,3,4,5): outcome = "🎳 Попал — несколько кеглей."
        else: outcome = "🎳 Промах."
        result_line = (f"🎉 <b>Угадал! +{payout:.2f} ⭐</b> (прибыль: {sign}{net:.2f} ⭐)" if won
                       else f"😞 <b>Не угадал. -{bet:.2f} ⭐</b>")
    elif game_type == "dice":
        outcome = f"🎲 Выпало: <b>{value}</b>"
        result_line = (f"🎉 <b>Выигрыш! +{payout:.2f} ⭐</b> (прибыль: {sign}{net:.2f} ⭐)" if won
                       else f"😞 <b>Проигрыш. -{bet:.2f} ⭐</b>")
    elif game_type == "slots":
        if value == 64: outcome = "🎰 <b>777 — Джекпот! 🏆</b>"
        elif value in (1,22,43): outcome = "🎰 <b>3 одинаковых! 🍀</b>"
        else: outcome = "🎰 Нет совпадений"
        result_line = (f"🎉 <b>Выигрыш! +{payout:.2f} ⭐</b>" if won else f"😞 <b>Проигрыш. -{bet:.2f} ⭐</b>")
    elif game_type == "darts":
        if value == 6: outcome = "🎯 Прямо в центр!"
        elif value == 1: outcome = "🎯 Отскок!"
        else: outcome = "🎯 Мимо!"
        result_line = (f"🎉 <b>Угадал! +{payout:.2f} ⭐</b> (прибыль: {sign}{net:.2f} ⭐)" if won
                       else f"😞 <b>Не угадал. -{bet:.2f} ⭐</b>")
    else:
        outcome, result_line = "", f"{'🎉 +' if won else '😞 -'}{(payout if won else bet):.2f} ⭐"

    return f"<b>{label}</b>\n\n{outcome}\n{result_line}\n\n💰 Баланс: <b>{float(balance):.2f} ⭐</b>"


@router.callback_query(lambda c: c.data == "menu:games")
async def cb_games_menu(callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    fsm = await state.get_state()
    if fsm in (GameStates.choose_dice_side, GameStates.choose_football_side,
               GameStates.choose_basketball_side, GameStates.choose_bowling_side,
               GameStates.choose_darts_side):
        data = await state.get_data()
        bet = data.get("bet", 0.0)
        if bet:
            db_user.stars_balance = round(float(db_user.stars_balance) + float(bet), 2)
            await session.commit()
    await state.clear()

    s_repo = SettingsRepository(session)
    games_enabled = await s_repo.get_bool("games_enabled", True)
    min_refs = max(0, await s_repo.get_int("games_min_refs", 3))

    if db_user.referrals_count < min_refs:
        text = (
            f"🎮 <b>Игры</b>\n\n"
            f"❌ Игры открываются после <b>{min_refs}</b> подтверждённых "
            f"{referrals_word(min_refs)}. У тебя: <b>{db_user.referrals_count}</b>."
        )
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_menu_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=back_to_menu_kb())
        await callback.answer()
        return

    configs = await _load_games_config(session)
    c_repo = ContentRepository(session)

    if not games_enabled:
        text = "🎮 <b>Игры</b>\n\nИгры временно недоступны."
    else:
        template = await c_repo.get_text("games")
        try:
            text = template.format(balance=f"{float(db_user.stars_balance):.2f}") if "{" in template else template
        except (KeyError, ValueError, IndexError):
            text = DEFAULT_TEXTS["games"].format(balance=f"{float(db_user.stars_balance):.2f}")
        text += "\n\nВыбери игру:"

    photo = await c_repo.get_photo("games")
    kb = games_menu_kb(configs)
    if photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("game:play:"))
async def cb_game_play(callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    await state.clear()
    game_type = callback.data.split(":")[2]
    if game_type not in GAME_TYPES:
        await callback.answer("❌ Неизвестная игра.", show_alert=True)
        return

    repo = SettingsRepository(session)
    if not await repo.get_bool("games_enabled", True):
        await callback.answer("🎮 Игры временно недоступны.", show_alert=True)
        return

    min_refs = max(0, await repo.get_int("games_min_refs", 3))
    if db_user.referrals_count < min_refs:
        await callback.answer(
            f"❌ Нужно минимум {min_refs} подтверждённых {referrals_word(min_refs)}. "
            f"У тебя: {db_user.referrals_count}",
            show_alert=True,
        )
        return
    if not await repo.get_bool(f"game_{game_type}_enabled", True):
        await callback.answer("🔒 Игра временно отключена.", show_alert=True)
        return

    daily_limit = await repo.get_int(f"game_{game_type}_daily_limit", 0)
    if daily_limit > 0:
        g_repo = GameRepository(session)
        cnt = await g_repo.daily_count(db_user.user_id, game_type)
        if cnt >= daily_limit:
            await callback.answer(f"⛔ Дневной лимит ({daily_limit} игр) исчерпан.", show_alert=True)
            return

    min_bet = await repo.get_float(f"game_{game_type}_min_bet", 1.0)
    bet_step = await repo.get_float(f"game_{game_type}_bet_step", 1.0)

    if db_user.stars_balance < min_bet:
        await callback.answer(f"❌ Минимальная ставка: {min_bet:.0f} ⭐", show_alert=True)
        return

    await state.set_state(GameStates.enter_bet)
    await state.update_data(game_type=game_type, bet_step=bet_step)

    try:
        await callback.message.edit_text(
            f"<b>{GAME_LABELS[game_type]}</b>\n\n"
            f"💰 Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>\n"
            f"Минимум: <b>{min_bet:.0f} ⭐</b>\n\nВведи ставку:",
            parse_mode="HTML",
            reply_markup=game_cancel_kb(),
        )
    except Exception:
        await callback.message.answer(
            f"<b>{GAME_LABELS[game_type]}</b>\n\nВведи ставку:",
            parse_mode="HTML", reply_markup=game_cancel_kb(),
        )
    await callback.answer()


@router.message(GameStates.enter_bet)
async def msg_bet_enter(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    game_type = data["game_type"]
    bet_step = data.get("bet_step", 1.0)

    try:
        bet = float(message.text.strip().replace(",", "."))
        if not math.isfinite(bet) or bet <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:", reply_markup=game_cancel_kb())
        return

    repo = SettingsRepository(session)
    min_bet = await repo.get_float(f"game_{game_type}_min_bet", 1.0)
    if bet < min_bet:
        await message.answer(f"❌ Мин. ставка: <b>{min_bet:.0f} ⭐</b>", parse_mode="HTML", reply_markup=game_cancel_kb())
        return

    if bet_step > 1.0 and abs(bet % bet_step) > 0.001:
        await message.answer(f"❌ Ставка кратна <b>{bet_step:.4g} ⭐</b>", parse_mode="HTML", reply_markup=game_cancel_kb())
        return

    if db_user.stars_balance < bet:
        await message.answer(f"❌ Недостаточно звёзд. Баланс: <b>{float(db_user.stars_balance):.2f} ⭐</b>", parse_mode="HTML", reply_markup=game_cancel_kb())
        return

    db_user.stars_balance = round(float(db_user.stars_balance) - bet, 2)
    await session.commit()

    side_states = {
        "dice": GameStates.choose_dice_side,
        "football": GameStates.choose_football_side,
        "basketball": GameStates.choose_basketball_side,
        "bowling": GameStates.choose_bowling_side,
        "darts": GameStates.choose_darts_side,
    }
    side_kbs = {
        "dice": dice_side_kb, "football": football_side_kb,
        "basketball": basketball_side_kb, "bowling": bowling_side_kb, "darts": darts_side_kb,
    }
    side_prompts = {
        "dice": "🎲 Выбери условие победы:",
        "football": "⚽ Гол или промах?",
        "basketball": "🏀 Поставь на исход броска:",
        "bowling": "🎳 Страйк или промах?",
        "darts": "🎯 Поставь на зону попадания:",
    }

    if game_type in side_states:
        await state.set_state(side_states[game_type])
        await state.update_data(bet=bet)
        await message.answer(
            f"<b>{GAME_LABELS[game_type]}</b>\n\nСтавка: <b>{bet:.0f} ⭐</b>\n\n{side_prompts[game_type]}",
            parse_mode="HTML", reply_markup=side_kbs[game_type](),
        )
        return

    await state.clear()
    try:
        won, payout, value = await _execute_game(
            message.bot, message.chat.id, session, db_user, game_type, bet,
        )
    except Exception:
        db_user.stars_balance = round(float(db_user.stars_balance) + bet, 2)
        await session.commit()
        await message.answer("⚠️ Ошибка игры. Ставка возвращена.", reply_markup=game_cancel_kb())
        return

    await message.answer(
        _result_text(game_type, won, bet, payout, value, db_user.stars_balance),
        parse_mode="HTML", reply_markup=game_result_kb(game_type),
    )


def _make_side_handler(game_type: str, state_cls):
    @router.callback_query(state_cls, lambda c: c.data and c.data.startswith(f"game:{game_type}:"))
    async def _handler(callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext) -> None:
        side = callback.data.split(":")[2]
        data = await state.get_data()
        bet = data["bet"]
        await state.clear()
        try:
            won, payout, value = await _execute_game(
                callback.bot, callback.message.chat.id, session, db_user, game_type, bet, side,
            )
        except Exception:
            db_user.stars_balance = round(float(db_user.stars_balance) + bet, 2)
            await session.commit()
            await callback.message.answer("⚠️ Ошибка игры. Ставка возвращена.", reply_markup=game_cancel_kb())
            await callback.answer()
            return
        await callback.message.answer(
            _result_text(game_type, won, bet, payout, value, db_user.stars_balance, side),
            parse_mode="HTML", reply_markup=game_result_kb(game_type),
        )
        await callback.answer()
    return _handler


_make_side_handler("dice", GameStates.choose_dice_side)
_make_side_handler("football", GameStates.choose_football_side)
_make_side_handler("basketball", GameStates.choose_basketball_side)
_make_side_handler("bowling", GameStates.choose_bowling_side)
_make_side_handler("darts", GameStates.choose_darts_side)
