import math
from datetime import datetime
from decimal import Decimal

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.auction import AuctionRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.auction import auction_cancel_kb, auction_kb
from bot.states.games import AuctionStates

router = Router()


def _format_timedelta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}ч {m}мин"
    elif m > 0:
        return f"{m}мин {s}с"
    return f"{s}с"


async def _notify_outbid(bot, prev_bidder_id: int, extra: float, round_, now: datetime) -> None:
    """DMs exactly the person who just lost the lead, showing the size of
    the bid that overtook them (not the cumulative prize pool, which is a
    different number shown separately) plus the true, correct way back:
    any next bid of at least 1 RP⭐️ reclaims the lead — the game never
    requires matching or exceeding the accumulated total."""
    remaining = (round_.end_at - now).total_seconds()
    time_str = _format_timedelta(remaining)
    try:
        await bot.send_message(
            prev_bidder_id,
            f"⚠️ <b>Вас перебили в аукционе!</b>\n\n"
            f"Новая ставка: <b>{extra:.2f} RP⭐️</b>\n"
            f"🏆 Призовой фонд: <b>{float(round_.prize_pool):.2f} RP⭐️</b>\n"
            f"👉 Чтобы вернуть лидерство — сделайте новую ставку (от 1 RP⭐️)\n"
            f"⏱ До конца аукциона: <b>{time_str}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


async def _auction_text(session: AsyncSession, db_user: User) -> tuple[str, bool]:
    repo = AuctionRepository(session)
    s_repo = SettingsRepository(session)

    enabled = await s_repo.get_bool("auction_enabled", True)
    if not enabled:
        return "🏺 <b>Аукцион</b>\n\nАукцион временно недоступен.", False

    round_ = await repo.get_active()
    if round_ is None:
        # No round yet — will be created by scheduler
        return (
            "🏺 <b>Аукцион</b>\n\n"
            "Аукцион скоро начнётся. Возвращайтесь позже!",
            False,
        )

    now = datetime.utcnow()
    remaining = (round_.end_at - now).total_seconds()
    time_str = _format_timedelta(remaining)

    commission = await s_repo.get_float("auction_commission", 0.20)
    winner_share = round(float(round_.prize_pool) * (1 - commission), 2)
    winner_percent = round((1 - commission) * 100)

    if round_.current_bidder_id:
        u_repo = UserRepository(session)
        leader = await u_repo.get(round_.current_bidder_id)
        leader_name = f"@{leader.username}" if leader and leader.username else f"ID {round_.current_bidder_id}"
        is_me = round_.current_bidder_id == db_user.user_id
        leader_line = f"👑 Лидер: <b>{leader_name}</b>" + (" (вы)" if is_me else "")
        bid_label = "💵 Моя ставка" if is_me else "💵 Тек. ставка"
        bid_line = f"{bid_label}: <b>{float(round_.current_bid):.2f} RP⭐️</b>"
    else:
        leader_line = "👑 Лидер: —"
        bid_line = "💵 Ставок пока нет"

    min_next = round(float(round_.current_bid) + 1.0, 2)

    text = (
        f"🏺 <b>Аукцион</b>\n\n"
        f"Каждая ставка пополняет общий призовой фонд — побеждает тот, "
        f"кто останется лидером к концу отсчёта. Перебитые ставки в фонде "
        f"и не возвращаются, поэтому побеждает только последний лидер.\n\n"
        f"🏆 Призовой фонд: <b>{float(round_.prize_pool):.2f} RP⭐️</b>\n"
        f"💰 Выигрыш победителя ({winner_percent}%): <b>{winner_share:.2f} RP⭐️</b>\n"
        f"{bid_line}\n"
        f"{leader_line}\n"
        f"⏱ До конца: <b>{time_str}</b>\n\n"
        f"Мин. ставка: <b>{min_next:.2f} RP⭐️</b>\n"
        f"💰 Ваш баланс: <b>{float(db_user.stars_balance):.2f} RP⭐️</b>"
    )
    return text, True


@router.callback_query(lambda c: c.data in ("menu:auction", "auction:refresh"))
async def cb_auction_menu(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    if callback.data == "menu:auction":
        await state.clear()

    text, has_active = await _auction_text(session, db_user)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=auction_kb(has_active))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=auction_kb(has_active))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("auction:bid:") and c.data != "auction:bid:custom")
async def cb_auction_bid(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    try:
        extra = float(callback.data.split(":")[2])
        if not math.isfinite(extra) or extra < 1:
            raise ValueError
    except (IndexError, ValueError):
        await callback.answer("❌ Минимальное повышение ставки: 1 RP⭐️.", show_alert=True)
        return

    repo = AuctionRepository(session)
    s_repo = SettingsRepository(session)

    if not await s_repo.get_bool("auction_enabled", True):
        await callback.answer("🔒 Аукцион недоступен.", show_alert=True)
        return

    round_ = await repo.get_active()
    if round_ is None:
        await callback.answer("⏳ Аукцион ещё не начался.", show_alert=True)
        return

    now = datetime.utcnow()
    if now >= round_.end_at:
        await callback.answer("🏁 Аукцион уже завершён.", show_alert=True)
        return

    min_bid = round(float(round_.current_bid) + 1.0, 2)
    total_bid = round(float(round_.current_bid) + extra, 2)
    if total_bid < min_bid:
        total_bid = min_bid

    if float(db_user.stars_balance) < extra:
        await callback.answer("❌ Недостаточно звёзд.", show_alert=True)
        return

    prev_bidder_id = round_.current_bidder_id

    db_user.stars_balance = round(float(db_user.stars_balance) - extra, 2)
    if not await repo.place_bid(
        round_,
        db_user.user_id,
        Decimal(str(total_bid)),
        Decimal(str(extra)),
    ):
        await callback.answer(
            "🔄 Ставка уже изменилась. Обновите аукцион и попробуйте снова.",
            show_alert=True,
        )
        return

    # Notify previous leader — only ever the immediately-preceding leader,
    # never anyone further back in the chain, since prev_bidder_id was read
    # right before this exact successful bid.
    if prev_bidder_id and prev_bidder_id != db_user.user_id:
        await _notify_outbid(callback.bot, prev_bidder_id, extra, round_, now)

    text, has_active = await _auction_text(session, db_user)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=auction_kb(has_active))
    except Exception:
        pass
    await callback.answer(f"✅ Ставка {total_bid:.2f} RP⭐️ принята!")


@router.callback_query(lambda c: c.data == "auction:bid:custom")
async def cb_auction_bid_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AuctionStates.enter_custom_bid)
    try:
        await callback.message.edit_text(
            "🏺 Введи сумму (сколько добавить к своей ставке):",
            reply_markup=auction_cancel_kb(),
        )
    except Exception:
        await callback.message.answer(
            "🏺 Введи сумму (сколько добавить к своей ставке):",
            reply_markup=auction_cancel_kb(),
        )
    await callback.answer()


@router.message(AuctionStates.enter_custom_bid)
async def msg_auction_custom(message: Message, state: FSMContext, db_user: User, session: AsyncSession) -> None:
    try:
        extra = float(message.text.strip().replace(",", "."))
        if not math.isfinite(extra):
            raise ValueError
        extra = round(extra, 2)
        if extra < 1:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Минимальное повышение — 1 RP⭐️:", reply_markup=auction_cancel_kb())
        return

    repo = AuctionRepository(session)
    s_repo = SettingsRepository(session)

    if not await s_repo.get_bool("auction_enabled", True):
        await message.answer("🔒 Аукцион недоступен.")
        await state.clear()
        return

    round_ = await repo.get_active()
    if round_ is None:
        await message.answer("⏳ Аукцион ещё не начался.")
        await state.clear()
        return

    now = datetime.utcnow()
    if now >= round_.end_at:
        await message.answer("🏁 Аукцион уже завершён.")
        await state.clear()
        return

    if float(db_user.stars_balance) < extra:
        await message.answer("❌ Недостаточно звёзд.", reply_markup=auction_cancel_kb())
        return

    prev_bidder_id = round_.current_bidder_id
    total_bid = round(float(round_.current_bid) + extra, 2)
    min_bid = round(float(round_.current_bid) + 1.0, 2)
    if total_bid < min_bid:
        total_bid = min_bid

    db_user.stars_balance = round(float(db_user.stars_balance) - extra, 2)
    if not await repo.place_bid(
        round_,
        db_user.user_id,
        Decimal(str(total_bid)),
        Decimal(str(extra)),
    ):
        await message.answer(
            "🔄 Ставка уже изменилась. Обновите аукцион и попробуйте снова.",
            reply_markup=auction_kb(True),
        )
        await state.clear()
        return
    await state.clear()

    if prev_bidder_id and prev_bidder_id != db_user.user_id:
        await _notify_outbid(message.bot, prev_bidder_id, extra, round_, now)

    text, has_active = await _auction_text(session, db_user)
    await message.answer(text, parse_mode="HTML", reply_markup=auction_kb(has_active))
