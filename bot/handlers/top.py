from html import escape

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.top import top_menu_kb
from bot.services.premium_emoji import (
    BRONZE_MEDAL,
    GOLD_MEDAL,
    NUM_10,
    NUM_DIGITS,
    NUMPAD,
    SILVER_MEDAL,
    premium,
)

router = Router()

MEDALS = [
    premium("🥇", GOLD_MEDAL),
    premium("🥈", SILVER_MEDAL),
    premium("🥉", BRONZE_MEDAL),
]
# Decorative header icon for the numbered-ranking lists below -- distinct
# artwork from the individual NUM_1..NUM_9 digits, shares NUM_1's
# verified fallback "1️⃣".
_NUMPAD_ICON = premium("1️⃣", NUMPAD)


def _rank_label(i: int) -> str:
    if i <= 3:
        return MEDALS[i - 1]
    if i == 10:
        # NUM_10's own registered fallback is "1️⃣" (see the constant's own
        # comment) -- the wrapped text below MUST be that exact fallback or
        # Telegram rejects the whole message, so this can't say "10" in the
        # tag itself even though the actual artwork does.
        return f'{premium("1️⃣", NUM_10)}.'
    digit_id = NUM_DIGITS.get(i)
    if not digit_id:
        # No premium "0" exists in the pack (only individual 1-9 digits and
        # the rank-10 special case above), so any other double-digit rank
        # falls back to plain text.
        return f"{i}."
    # Telegram rejects a <tg-emoji> tag with ENTITY_TEXT_INVALID unless its
    # inner text matches that specific custom emoji's own registered
    # fallback -- confirmed live, a bare "4" crashed this handler for every
    # user. The real fallback is the standard keycap sequence (digit +
    # U+FE0F variation selector + U+20E3 combining enclosing keycap), e.g.
    # "4️⃣", exactly as given in the source pack's own HTML export.
    return f'{premium(f"{i}️⃣", digit_id)}.'


def _format_top(users: list, field: str, title: str, label: str) -> str:
    lines = [f"{_NUMPAD_ICON} 🏆 <b>{title}</b>\n"]
    for i, u in enumerate(users, 1):
        medal = _rank_label(i)
        name = escape(f"@{u.username}" if u.username else u.first_name)
        value = float(getattr(u, field))
        lines.append(f"{medal} {name} — <b>{value:.0f} {label}</b>")
    return "\n".join(lines)


@router.callback_query(lambda c: c.data == "menu:top")
async def cb_top_menu(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    c_repo = ContentRepository(session)
    text = await c_repo.get_text("top")
    photo = await c_repo.get_photo("top")
    kb = top_menu_kb()
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


@router.callback_query(lambda c: c.data == "top:referrals")
async def cb_top_referrals(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = UserRepository(session)
    users = await repo.top_by_referrals(10)
    text = _format_top(users, "referrals_count", "Топ по рефералам", "👥️")
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=top_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=top_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "top:balance")
async def cb_top_balance(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = UserRepository(session)
    users = await repo.top_by_balance(10)
    text = _format_top(users, "stars_balance", "Топ по звёздам", "RP⭐️")
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=top_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=top_menu_kb())
    await callback.answer()
