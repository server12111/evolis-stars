from html import escape

from aiogram import Router
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import GameSession, User
from bot.database.repositories.game import GameRepository
from bot.database.repositories.user import UserRepository

router = Router()

_MEDALS = ["🥇", "🥈", "🥉"]

_INFO_TEXT = (
    "📖 <b>Команды EvolisStars</b>\n\n"
    "💰 <b>Баланс</b>\n"
    "б / бал / балик / баланс — показать свой баланс\n\n"
    "🎰 <b>Игры</b>\n"
    "ред &lt;ставка&gt; / блек &lt;ставка&gt; — Рулетка (угадай цвет, ×1.6)\n"
    "башня &lt;ставка&gt; — Башня (10 уровней, забирай выигрыш в любой момент)\n"
    "лабиринт &lt;ставка&gt; — Лабиринт (иди дальше или забирай выигрыш)\n"
    "двери &lt;ставка&gt; — Двери (10 уровней, 2 из 4 дверей — мина)\n\n"
    "🎁 <b>Промо и бонусы</b>\n"
    "промокод &lt;код&gt; — активировать промокод чата\n"
    "бонус &lt;код&gt; — активировать бонусный код чата\n"
    "выбрать &lt;код&gt; — (только владелец, ответом на сообщение победителя) выбрать победителя конкурса\n\n"
    "📊 <b>Топы и статистика</b>\n"
    "топ чатов — топ 10 чатов по числу участников\n"
    "топ — топ 10 пользователей по звёздам\n"
    "лог — последние 10 игр в рулетку\n"
    "профиль / пас — твой юзернейм, баланс и число сыгранных игр (всего и в этом чате)\n\n"
    "⚙️ <b>Владельцу чата</b>\n"
    "/EvolisOpen — панель управления чатом (только владелец, от 250 участников)\n\n"
    "⭐️ Лучший реферальный бот — @EvolisStarsBot"
)


def _matches_info(message: Message) -> bool:
    return bool(message.text and message.text.strip().lower() in {"команды", "инфо"})


def _matches_top(message: Message) -> bool:
    return bool(message.text and message.text.strip().lower() == "топ")


def _matches_log(message: Message) -> bool:
    return bool(message.text and message.text.strip().lower() == "лог")


def _matches_profile(message: Message) -> bool:
    return bool(message.text and message.text.strip().lower() in {"профиль", "пас"})


@router.message(_matches_profile)
async def msg_group_profile(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    user = await session.get(User, message.from_user.id)
    if user is None:
        await message.reply("❌ Нужно быть зарегистрированным в боте — напиши /start в личных сообщениях.")
        return

    g_repo = GameRepository(session)
    total_games = await g_repo.user_total_count(user.user_id)
    chat_games = await g_repo.user_chat_count(user.user_id, message.chat.id)
    username_display = f"@{user.username}" if user.username else escape(user.first_name)
    vip_line = "💎 <b>VIP</b>\n" if user.is_vip else ""

    text = (
        f"🪪 <b>Профиль</b>\n\n"
        f"👤 {username_display}\n"
        f"{vip_line}"
        f"💰 Баланс: <b>{float(user.stars_balance):.2f} ⭐</b>\n"
        f"🎮 Игр сыграно всего: <b>{total_games}</b>\n"
        f"💬 Игр в этом чате: <b>{chat_games}</b>"
    )
    await message.reply(text, parse_mode="HTML")


@router.message(_matches_info)
async def msg_info(message: Message) -> None:
    await message.answer(_INFO_TEXT, parse_mode="HTML")


@router.message(_matches_top)
async def msg_top_users(message: Message, session: AsyncSession) -> None:
    users = await UserRepository(session).top_by_balance(10)
    if not users:
        await message.reply("Пока нет пользователей.")
        return
    lines = ["🏆 <b>Топ 10 пользователей по звёздам</b>\n"]
    for i, user in enumerate(users, 1):
        medal = _MEDALS[i - 1] if i <= 3 else f"{i}."
        name = escape(f"@{user.username}" if user.username else user.first_name)
        lines.append(f"{medal} {name} — <b>{float(user.stars_balance):.0f} ⭐</b>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(_matches_log)
async def msg_roulette_log(message: Message, session: AsyncSession) -> None:
    result = await session.execute(
        select(GameSession, User)
        .join(User, User.user_id == GameSession.user_id)
        .where(GameSession.game_type == "roulette")
        .order_by(GameSession.played_at.desc())
        .limit(10)
    )
    rows = result.all()
    if not rows:
        await message.reply("Пока нет сыгранных партий в рулетку.")
        return

    lines = ["🎰 <b>Последние 10 игр в рулетку</b>\n"]
    for game, user in rows:
        name = escape(f"@{user.username}" if user.username else user.first_name)
        if game.result == "win":
            lines.append(f"{name}: ставка {float(game.bet):.2f} ⭐ — 🎉 +{float(game.payout):.2f} ⭐")
        else:
            lines.append(f"{name}: ставка {float(game.bet):.2f} ⭐ — ❌ -{float(game.bet):.2f} ⭐")
    await message.answer("\n".join(lines), parse_mode="HTML")
