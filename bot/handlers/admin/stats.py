from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import Chat, ChatAdSend, ChatBonusUse, ChatLinkClick, ChatPromoUse, User
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.user import UserRepository
from bot.database.repositories.game import GameRepository
from bot.database.repositories.withdrawal import WithdrawalRepository
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.admin.main import admin_main_kb
from bot.keyboards.admin.stats import stats_back_kb, stats_scope_kb

router = Router()
settings = get_settings()

GAME_TYPES_ALL = ["football", "basketball", "bowling", "dice", "slots", "darts", "wheel", "case_1", "case_3", "case_5"]
CHAT_GAME_TYPES = [("roulette", "roulette"), ("tower", "chat_tower"), ("maze", "maze"), ("doors", "doors")]


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
async def cb_admin_stats(callback: CallbackQuery, db_user: User) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    text = "📊 <b>Статистика</b>\n\nВыбери раздел:"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=stats_scope_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=stats_scope_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:stats:bot")
async def cb_admin_stats_bot(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
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
        "🤖 <b>Статистика бота</b>\n",
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
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=stats_back_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=stats_back_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:stats:chats")
async def cb_admin_stats_chats(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    status_rows = await session.execute(select(Chat.status, func.count()).group_by(Chat.status))
    status_counts = dict(status_rows.all())
    active = status_counts.get("active", 0)
    pending = status_counts.get("pending", 0)
    left = status_counts.get("left", 0)

    total_members = await session.scalar(
        select(func.coalesce(func.sum(Chat.member_count), 0)).where(Chat.status == "active")
    ) or 0
    ad_sends = await session.scalar(select(func.count()).select_from(ChatAdSend)) or 0
    clicks = await session.scalar(select(func.count()).select_from(ChatLinkClick)) or 0
    promo_uses = await session.scalar(select(func.count()).select_from(ChatPromoUse)) or 0
    bonus_uses = await session.scalar(select(func.count()).select_from(ChatBonusUse)) or 0

    lines = [
        "💬 <b>Статистика чатов</b>\n",
        "📌 <b>Чаты</b>",
        f"  Активных: <b>{active}</b>",
        f"  Ниже порога: <b>{pending}</b>",
        f"  Покинутых: <b>{left}</b>",
        f"  Участников (в активных): <b>{total_members}</b>\n",
        "🎁 <b>Промо/бонусы</b>",
        f"  Промокодов активировано: <b>{promo_uses}</b>",
        f"  Бонусов выдано: <b>{bonus_uses}</b>\n",
        "📣 <b>Реклама</b>",
        f"  Показов (ЛС) отправлено: <b>{ad_sends}</b>",
        f"  Кликов по кнопкам: <b>{clicks}</b>\n",
        "🎮 <b>Заработок с игр в чатах</b>",
    ]

    s_repo = SettingsRepository(session)
    for label, prefix in CHAT_GAME_TYPES:
        bet = await s_repo.get_float(f"{prefix}_total_bet", 0)
        payout = await s_repo.get_float(f"{prefix}_total_payout", 0)
        profit = round(bet - payout, 2)
        lines.append(f"  {label}: bet={bet:.1f} pay={payout:.1f} profit=<b>{profit:.1f} ⭐</b>")

    top_chats = await ChatRepository(session).top_by_member_count(10)
    lines.append("\n🏆 <b>Топ 10 чатов по участникам</b>")
    if top_chats:
        for i, chat in enumerate(top_chats, 1):
            title = escape(chat.title or f"Chat {chat.chat_id}")
            lines.append(f"  {i}. {title} — <b>{chat.member_count}</b>")
    else:
        lines.append("  Пока нет активных чатов.")

    text = "\n".join(lines)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=stats_back_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=stats_back_kb())
    await callback.answer()
