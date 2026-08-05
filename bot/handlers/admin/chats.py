from html import escape

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.game import GameRepository
from bot.database.repositories.user import UserRepository
from bot.handlers.admin.stats import _is_admin

router = Router()

_PAGE_SIZE = 10


def _chat_link(chat) -> str:
    """Public chats link via @username, private chats via a previously
    saved invite link (never freshly generated), otherwise plain text."""
    title = escape(chat.title or str(chat.chat_id))
    if chat.username:
        return f'<a href="https://t.me/{chat.username}">{title}</a>'
    if chat.invite_link:
        return f'<a href="{escape(chat.invite_link)}">{title}</a>'
    return title


async def _render_row(session: AsyncSession, rank: int, chat) -> str:
    owner_line = "—"
    if chat.owner_user_id is not None:
        owner = await UserRepository(session).get(chat.owner_user_id)
        owner_line = (
            f"@{escape(owner.username)}" if owner and owner.username
            else (escape(owner.first_name) if owner else f"ID {chat.owner_user_id}")
        )
    games_played = sum((await GameRepository(session).chat_stats(chat.chat_id)).values())
    added = chat.added_at.strftime("%d.%m.%Y") if chat.added_at else "—"

    return (
        f"<b>{rank}.</b> {_chat_link(chat)}\n"
        f"👥 {chat.member_count} | 👤 {owner_line} | 📅 {added}\n"
        f"🎮 Игр сыграно: {games_played}"
    )


async def _render_page(session: AsyncSession, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = await ChatRepository(session).connected_count()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chats = await ChatRepository(session).paginated_by_member_count(page * _PAGE_SIZE, _PAGE_SIZE)

    if not chats:
        text = "💬 <b>Список чатов</b>\n\nБот пока не подключён ни к одному чату."
    else:
        rows = [
            await _render_row(session, page * _PAGE_SIZE + i, chat)
            for i, chat in enumerate(chats, 1)
        ]
        text = (
            f"💬 <b>Список чатов</b> ({total})\n"
            f"Страница {page + 1}/{total_pages}\n\n" + "\n\n".join(rows)
        )

    builder_row = []
    if page > 0:
        builder_row.append(InlineKeyboardButton(text="◀️ Пред", callback_data=f"admin:chatslist:{page - 1}"))
    if page < total_pages - 1:
        builder_row.append(InlineKeyboardButton(text="След ▶️", callback_data=f"admin:chatslist:{page + 1}"))
    kb_rows = [builder_row] if builder_row else []
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@router.callback_query(lambda c: c.data == "admin:chatslist")
async def cb_admin_chatslist(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    text, kb = await _render_page(session, 0)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:chatslist:"))
async def cb_admin_chatslist_page(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    try:
        page = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0
    text, kb = await _render_page(session, page)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    await callback.answer()
