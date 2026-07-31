import math
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.lottery import LotteryRepository
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.games import cancel_kb as lottery_cancel_kb
from bot.keyboards.admin.games import game_coeffs_kb, game_config_kb, games_admin_kb
from bot.keyboards.admin.lottery import lottery_admin_kb, lottery_end_type_kb
from bot.keyboards.admin.main import back_to_admin_kb
from bot.states.admin import AdminGameStates, AdminLotteryStates

router = Router()

GAME_SETTINGS = ["football", "basketball", "bowling", "dice", "darts", "slots"]

# Real per-game coefficient settings keys actually read by bot/handlers/games.py.
# (suffix, label) — the stored key is f"game_{game_type}_{suffix}".
GAME_COEFF_KEYS: dict[str, list[tuple[str, str]]] = {
    "football": [("coeff_goal", "Гол"), ("coeff_miss", "Промах")],
    "basketball": [
        ("coeff_clean", "Чистый гол"),
        ("coeff_any", "Любой гол"),
        ("coeff_stuck", "Застрял"),
        ("coeff_miss", "Промах"),
    ],
    "bowling": [
        ("coeff_strike", "Страйк"),
        ("coeff_partial", "Частично"),
        ("coeff_miss", "Промах"),
    ],
    "dice": [("coeff", "Кубики")],
    "slots": [("coeff1", "777"), ("coeff2", "3 одинаковых")],
    "darts": [("coeff_bullseye", "В центр"), ("coeff_bounce", "Отскок")],
}


def _lottery_end_value(end_type: str, value: float) -> float:
    if end_type == "time":
        return (datetime.utcnow() + timedelta(hours=value)).timestamp()
    return value


@router.callback_query(lambda c: c.data == "admin:games")
async def cb_games(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    repo = SettingsRepository(session)
    configs = {}
    for game in GAME_SETTINGS:
        configs[game] = {"enabled": await repo.get_bool(f"game_{game}_enabled", True)}
    try:
        await callback.message.edit_text(
            "🎮 <b>Настройки игр</b>",
            parse_mode="HTML",
            reply_markup=games_admin_kb(configs),
        )
    except Exception:
        await callback.message.answer("🎮 <b>Настройки игр</b>", parse_mode="HTML", reply_markup=games_admin_kb(configs))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:game_toggle:"))
async def cb_game_toggle(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    game_type = callback.data.split(":")[2]
    repo = SettingsRepository(session)
    current = await repo.get_bool(f"game_{game_type}_enabled", True)
    await repo.set(f"game_{game_type}_enabled", "0" if current else "1")
    new_state = not current
    await callback.answer(f"{'✅ Включено' if new_state else '❌ Отключено'}")
    await cb_games(callback, db_user, session, state)


@router.callback_query(lambda c: c.data and c.data.startswith("admin:game_cfg:"))
async def cb_game_edit(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    game_type = callback.data.split(":")[2]
    repo = SettingsRepository(session)
    enabled = await repo.get_bool(f"game_{game_type}_enabled", True)
    coeff_keys = GAME_COEFF_KEYS.get(game_type, [])

    lines = [
        f"⚙️ <b>{game_type.title()}</b>\n",
        f"Статус: {'✅ включена' if enabled else '❌ отключена'}",
    ]
    if coeff_keys:
        for suffix, label in coeff_keys:
            value = await repo.get_float(f"game_{game_type}_{suffix}", 0.0)
            lines.append(f"{label}: <b>{value:.2f}x</b>")
        min_bet = await repo.get_float(f"game_{game_type}_min_bet", 1.0)
        lines.append(f"Мин. ставка: <b>{min_bet:.1f} ⭐</b>")
    text = "\n".join(lines)
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=game_config_kb(game_type, enabled, bool(coeff_keys)),
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="HTML",
            reply_markup=game_config_kb(game_type, enabled, bool(coeff_keys)),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:game_coeffs:"))
async def cb_game_coeffs(callback: CallbackQuery, db_user: User) -> None:
    if not _is_admin(db_user): return
    game_type = callback.data.split(":")[2]
    coeff_keys = GAME_COEFF_KEYS.get(game_type, [])
    if not coeff_keys:
        await callback.answer("У этой игры нет настраиваемых коэффициентов.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            f"⚙️ <b>{game_type.title()}</b>\n\nВыбери коэффициент для изменения:",
            parse_mode="HTML",
            reply_markup=game_coeffs_kb(game_type, coeff_keys),
        )
    except Exception:
        await callback.message.answer(
            f"⚙️ <b>{game_type.title()}</b>\n\nВыбери коэффициент для изменения:",
            parse_mode="HTML",
            reply_markup=game_coeffs_kb(game_type, coeff_keys),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:game_coef_pick:"))
async def cb_game_coef_pick(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    _, _, game_type, suffix = callback.data.split(":", 3)
    coeff_keys = dict(GAME_COEFF_KEYS.get(game_type, []))
    label = coeff_keys.get(suffix, suffix)
    await state.set_state(AdminGameStates.enter_value)
    await state.update_data(game_type=game_type, param=suffix)
    await callback.message.answer(
        f"Введи новый коэффициент «{label}» для {game_type} (напр: 1.9):",
        reply_markup=lottery_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:game_minbet:"))
async def cb_game_minbet(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    game_type = callback.data.split(":")[2]
    await state.set_state(AdminGameStates.enter_value)
    await state.update_data(game_type=game_type, param="min_bet")
    await callback.message.answer(f"Введи мин. ставку для {game_type} (напр: 1):", reply_markup=lottery_cancel_kb())
    await callback.answer()


@router.message(AdminGameStates.enter_value)
async def msg_game_value(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    try:
        value = float(message.text.strip().replace(",", "."))
        if not math.isfinite(value) or value <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:")
        return
    data = await state.get_data()
    await state.clear()
    repo = SettingsRepository(session)
    await repo.set(f"game_{data['game_type']}_{data['param']}", str(value))
    await message.answer(f"✅ Сохранено: <b>{data['game_type']}.{data['param']} = {value}</b>", parse_mode="HTML", reply_markup=back_to_admin_kb())


# ---- Lottery admin ----

@router.callback_query(lambda c: c.data == "admin:lottery")
async def cb_lottery(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    repo = LotteryRepository(session)
    active = await repo.get_active()
    if active:
        text = (
            f"🎟 <b>Активная лотерея #{active.id}</b>\n\n"
            f"Билетов продано: <b>{active.tickets_sold}</b>\n"
            f"Призовой фонд: <b>{float(active.prize_pool):.2f} ⭐</b>\n"
            f"Лимит билетов: <b>{active.ticket_limit or '∞'}</b>\n"
            f"Цена билета: <b>{float(active.ticket_price):.1f} ⭐</b>"
        )
    else:
        text = "🎟 <b>Лотерея</b>\n\nАктивной лотереи нет."
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=lottery_admin_kb(bool(active)))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=lottery_admin_kb(bool(active)))
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:lottery_new")
async def cb_lottery_new(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.set_state(AdminLotteryStates.enter_ticket_price)
    await callback.message.answer("💰 Введи цену одного билета (⭐):", reply_markup=lottery_cancel_kb())
    await callback.answer()


@router.message(AdminLotteryStates.enter_ticket_price)
async def msg_lottery_price(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    try:
        price = float(message.text.strip().replace(",", "."))
        if not math.isfinite(price):
            raise ValueError
        price = round(price, 2)
        if price <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:", reply_markup=lottery_cancel_kb())
        return
    await state.update_data(ticket_price=price)
    await state.set_state(AdminLotteryStates.enter_ticket_limit)
    await message.answer("🎟 Максимум билетов (0 = без лимита):", reply_markup=lottery_cancel_kb())


@router.message(AdminLotteryStates.enter_ticket_limit)
async def msg_lottery_limit(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    try:
        limit = int(message.text.strip())
        if limit < 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи неотрицательное целое:", reply_markup=lottery_cancel_kb())
        return
    await state.update_data(ticket_limit=limit)
    await state.set_state(AdminLotteryStates.choose_end_type)
    await message.answer("⏱ Выбери условие завершения лотереи:", reply_markup=lottery_end_type_kb())


@router.callback_query(AdminLotteryStates.choose_end_type, lambda c: c.data and c.data.startswith("lottery_end:"))
async def cb_lottery_end_type(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    end_type = callback.data.split(":")[1]
    await state.update_data(end_type=end_type)
    prompts = {
        "tickets": "🎟 Введи кол-во билетов для розыгрыша:",
        "time": "⏰ Введи кол-во часов до розыгрыша:",
        "commission": "💰 Введи общую сумму сбора для розыгрыша (⭐):",
    }
    await state.set_state(AdminLotteryStates.enter_end_value)
    await callback.message.answer(prompts[end_type], reply_markup=lottery_cancel_kb())
    await callback.answer()


@router.message(AdminLotteryStates.enter_end_value)
async def msg_lottery_end_value(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    try:
        val = float(message.text.strip().replace(",", "."))
        if not math.isfinite(val) or val <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:", reply_markup=lottery_cancel_kb())
        return
    data = await state.get_data()
    if data["end_type"] == "tickets":
        if not val.is_integer():
            await message.answer(
                "❌ Количество билетов должно быть целым числом:",
                reply_markup=lottery_cancel_kb(),
            )
            return
        ticket_limit = int(data["ticket_limit"])
        if ticket_limit > 0 and int(val) > ticket_limit:
            await message.answer(
                f"❌ Условие розыгрыша не может превышать лимит {ticket_limit} билетов:",
                reply_markup=lottery_cancel_kb(),
            )
            return
    end_value = _lottery_end_value(data["end_type"], val)
    await state.clear()
    repo = LotteryRepository(session)
    lottery = await repo.create(
        ticket_price=data["ticket_price"],
        ticket_limit=data["ticket_limit"],
        end_type=data["end_type"],
        end_value=end_value,
    )
    await message.answer(
        f"✅ <b>Лотерея #{lottery.id} создана!</b>\n\nЦена билета: {float(lottery.ticket_price):.1f} ⭐",
        parse_mode="HTML",
        reply_markup=back_to_admin_kb(),
    )


@router.callback_query(lambda c: c.data == "admin:lottery_draw")
async def cb_lottery_draw(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    repo = LotteryRepository(session)
    active = await repo.get_active()
    if not active:
        await callback.answer("Нет активной лотереи.", show_alert=True)
        return
    winner_id = await repo.draw_random(active)
    if not winner_id:
        await callback.answer("❌ Нет участников.", show_alert=True)
        return
    prize = float(active.prize_pool)
    winner = await session.get(User, winner_id)
    if not winner:
        # Don't close out the lottery over a phantom winner — the prize
        # would otherwise be marked paid without ever being credited.
        await callback.answer(
            "⚠️ Победитель не найден в базе. Розыгрыш отменён, попробуйте ещё раз.",
            show_alert=True,
        )
        return
    winner.stars_balance = round(float(winner.stars_balance) + prize, 2)
    if not await repo.finish(active, winner_id):
        await callback.answer(
            "Лотерея уже изменилась. Повторите розыгрыш.",
            show_alert=True,
        )
        return
    try:
        await callback.bot.send_message(
            winner_id,
            f"🎉 <b>Поздравляем!</b> Вы выиграли в лотерее!\n\nПриз: <b>{float(active.prize_pool):.2f} ⭐</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer(f"✅ Победитель: {winner_id}", show_alert=True)
    await cb_lottery(callback, db_user, session, state)


@router.callback_query(lambda c: c.data == "admin:lottery_cancel")
async def cb_lottery_cancel_active(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    repo = LotteryRepository(session)
    active = await repo.get_active()
    if not active:
        await callback.answer("Нет активной лотереи.", show_alert=True)
        return
    if active.tickets_sold > 0:
        await callback.answer(
            "❌ Нельзя отменить лотерею с купленными билетами. Проведите розыгрыш.",
            show_alert=True,
        )
        return
    if not await repo.cancel(active):
        await callback.answer(
            "❌ Лотерея уже изменилась; отмена невозможна.",
            show_alert=True,
        )
        return
    await callback.answer("✅ Лотерея отменена.")
    await cb_lottery(callback, db_user, session)
