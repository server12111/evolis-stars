from html import escape

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.top import top_menu_kb

router = Router()

MEDALS = ["🥇", "🥈", "🥉"]


def _format_top(users: list, field: str, title: str, label: str) -> str:
    lines = [f"🏆 <b>{title}</b>\n"]
    for i, u in enumerate(users, 1):
        medal = MEDALS[i - 1] if i <= 3 else f"{i}."
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
    text = _format_top(users, "referrals_count", "Топ по рефералам", "👥")
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=top_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=top_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "top:balance")
async def cb_top_balance(callback: CallbackQuery, session: AsyncSession) -> None:
    repo = UserRepository(session)
    users = await repo.top_by_balance(10)
    text = _format_top(users, "stars_balance", "Топ по звёздам", "⭐")
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=top_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=top_menu_kb())
    await callback.answer()
