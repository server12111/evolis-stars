from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.user import UserRepository
from bot.database.repositories.game import GameRepository
from bot.database.repositories.withdrawal import WithdrawalRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.admin.main import admin_main_kb, back_to_admin_kb

router = Router()
settings = get_settings()

GAME_TYPES_ALL = ["football", "basketball", "bowling", "dice", "slots", "darts", "wheel", "case_1", "case_3", "case_5"]


def _is_admin(user: User) -> bool:
    return user.is_admin or user.user_id in settings.admin_id_list


@router.message(Command("admin"))
async def cmd_admin(message: Message, db_user: User) -> None:
    if not _is_admin(db_user):
        await message.answer("❌ Нет доступа.")
        return
    await message.answer(
        "🔐 <b>Админ-панель</b>\n\nВыбери раздел:",
        parse_mode="HTML",
        reply_markup=admin_main_kb(),
    )


@router.callback_query(lambda c: c.data == "admin:main")
async def cb_admin_main(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_text(
            "🔐 <b>Админ-панель</b>\n\nВыбери раздел:",
            parse_mode="HTML",
            reply_markup=admin_main_kb(),
        )
    except Exception:
        await callback.message.answer("🔐 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=admin_main_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    u_repo = UserRepository(session)
    g_repo = GameRepository(session)
    w_repo = WithdrawalRepository(session)

    total_users = await u_repo.total_count()
    today_users = await u_repo.today_count()
    total_balance = await u_repo.total_balance()
    pending = await w_repo.pending_count()
    approved_sum = await w_repo.approved_sum()
    rejected = await w_repo.rejected_count()
    total_games = await g_repo.total_games()
    today_games = await g_repo.today_games()
    wins = await g_repo.win_count()
    win_rate = round(wins / total_games * 100, 1) if total_games else 0

    lines = [
        "📊 <b>Статистика</b>\n",
        "👥 <b>Пользователи</b>",
        f"  Всего: <b>{total_users}</b>",
        f"  Новых сегодня: <b>{today_users}</b>",
        f"  Общий баланс: <b>{total_balance:.2f} ⭐</b>\n",
        "💸 <b>Выплаты</b>",
        f"  В ожидании: <b>{pending}</b>",
        f"  Выведено: <b>{approved_sum:.2f} ⭐</b>",
        f"  Отклонено: <b>{rejected}</b>\n",
        "🎮 <b>Игры</b>",
        f"  Всего игр: <b>{total_games}</b>",
        f"  Сегодня: <b>{today_games}</b>",
        f"  Побед: <b>{wins}</b>",
        f"  Процент побед: <b>{win_rate}%</b>\n",
        "📈 <b>По играм (прибыль казино):</b>",
    ]

    s_repo = SettingsRepository(session)
    for gt in GAME_TYPES_ALL:
        bet = await s_repo.get_float(f"{gt}_total_bet", 0)
        payout = await s_repo.get_float(f"{gt}_total_payout", 0)
        profit = round(bet - payout, 2)
        lines.append(f"  {gt}: bet={bet:.1f} pay={payout:.1f} profit=<b>{profit:.1f} ⭐</b>")

    text = "\n".join(lines)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_admin_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=back_to_admin_kb())
    await callback.answer()
