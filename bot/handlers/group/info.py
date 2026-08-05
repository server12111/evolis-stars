from html import escape

from aiogram import Router
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import GameSession, User
from bot.database.repositories.chat_membership import ChatMembershipRepository
from bot.database.repositories.game import GameRepository
from bot.keyboards.group.registration import REGISTRATION_REQUIRED_TEXT, registration_required_kb
from bot.services.chat_games import COLOR_EMOJI

router = Router()
settings = get_settings()

_MEDALS = ["🥇", "🥈", "🥉"]

_INFO_TEXT = (
    "📖 <b>Команды EvolisStars</b>\n\n"
    "💰 <b>Баланс</b>\n"
    "б / бал / балик / баланс — показать свой баланс\n\n"
    "🎰 <b>Игры</b>\n"
    "ред &lt;ставка&gt; / блек &lt;ставка&gt; / грин &lt;ставка&gt; — Рулетка (угадай цвет)\n"
    "башня &lt;ставка&gt; — Башня (10 уровней, забирай выигрыш в любой момент)\n"
    "лабиринт &lt;ставка&gt; — Лабиринт (иди дальше или забирай выигрыш)\n"
    "двери &lt;ставка&gt; — Двери (10 уровней, 2 из 4 дверей — мина)\n\n"
    "🎁 <b>Промо и бонусы</b>\n"
    "промокод &lt;код&gt; — активировать промокод чата\n"
    "бонус &lt;код&gt; — активировать бонусный код чата\n"
    "выбрать &lt;код&gt; — (только владелец, ответом на сообщение победителя) выбрать победителя конкурса\n\n"
    "📊 <b>Топы и статистика</b>\n"
    "топ чатов — топ 10 чатов по числу участников\n"
    "топ — топ 10 пользователей этого чата по звёздам\n"
    "лог — последние 10 игр в рулетку в этом чате\n"
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
        await message.reply(REGISTRATION_REQUIRED_TEXT, reply_markup=registration_required_kb(settings.bot_username))
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


async def render_top_users_text(session: AsyncSession, chat_id: int) -> str | None:
    """Shared by the in-group "топ" command and the private chat panel.
    Returns None if this chat has no members with a balance yet."""
    users = await ChatMembershipRepository(session).top_users_by_balance(chat_id, 10)
    if not users:
        return None
    lines = ["🏆 <b>Топ 10 пользователей чата по звёздам</b>\n"]
    for i, user in enumerate(users, 1):
        medal = _MEDALS[i - 1] if i <= 3 else f"{i}."
        name = escape(f"@{user.username}" if user.username else user.first_name)
        lines.append(f"{medal} {name} — <b>{float(user.stars_balance):.0f} ⭐</b>")
    return "\n".join(lines)


@router.message(_matches_top)
async def msg_top_users(message: Message, session: AsyncSession) -> None:
    text = await render_top_users_text(session, message.chat.id)
    if text is None:
        await message.reply("Пока нет пользователей.")
        return
    await message.answer(text, parse_mode="HTML")


async def render_roulette_log_text(session: AsyncSession, chat_id: int) -> str | None:
    """Shared by the in-group "лог" command and the private chat panel.
    Returns None if this chat has no roulette history yet."""
    result = await session.execute(
        select(
            GameSession.bet, GameSession.result_choice, GameSession.result, GameSession.payout,
            User.username, User.first_name,
        )
        .join(User, User.user_id == GameSession.user_id)
        .where(GameSession.game_type == "roulette", GameSession.chat_id == chat_id)
        .order_by(GameSession.played_at.desc())
        .limit(10)
    )
    rows = result.all()
    if not rows:
        return None

    lines = ["🎰 <b>Последние 10 игр в рулетку</b>\n"]
    for bet, result_choice, game_result, payout, username, first_name in rows:
        emoji = COLOR_EMOJI.get(result_choice, "⚪️")
        val = float(bet)
        bet_str = f"{val:.0f}" if val == int(val) else f"{val:.2f}".rstrip("0").rstrip(".")
        name = escape(f"@{username}" if username else (first_name or "—"))
        if game_result == "win":
            outcome = f"✅ +{float(payout):.2f} ⭐"
        else:
            outcome = f"❌ -{bet_str} ⭐"
        lines.append(f"<code>{name}</code>: {bet_str}⭐ — {emoji} — {outcome}")
    return "\n".join(lines)


@router.message(_matches_log)
async def msg_roulette_log(message: Message, session: AsyncSession) -> None:
    text = await render_roulette_log_text(session, message.chat.id)
    if text is None:
        await message.reply("Пока нет сыгранных партий в рулетку.")
        return
    await message.answer(text, parse_mode="HTML")
