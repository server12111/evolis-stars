import os
from datetime import datetime

from aiogram import Router
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile

from bot.config import get_settings
from bot.database.models import User
from bot.handlers.admin.stats import _is_admin
from bot.keyboards.admin.logs import logs_menu_kb

router = Router()
settings = get_settings()

TAIL_LINES = 300


def tail_log_lines(path: str, n: int = TAIL_LINES) -> str:
    """Return the last n lines of the log file, or an explanatory message
    if it doesn't exist yet (e.g. right after a fresh deploy, before
    anything has been logged)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return "Файл лога пока не создан."
    return "".join(lines[-n:]) if lines else "Файл лога пока пуст."


@router.callback_query(lambda c: c.data == "admin:logs")
async def cb_logs_menu(callback: CallbackQuery, db_user: User) -> None:
    if not _is_admin(db_user): return
    text = (
        "📄 <b>Логи бота</b>\n\n"
        "«Последние 300 строк» — быстрый срез для разбора текущей проблемы. "
        "«Весь файл» — полный текущий лог (ротация: до 10 МБ, старые версии не прикладываются)."
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=logs_menu_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=logs_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:logs_tail")
async def cb_logs_tail(callback: CallbackQuery, db_user: User) -> None:
    if not _is_admin(db_user): return
    content = tail_log_lines(settings.log_file_path, TAIL_LINES)
    filename = f"bot_log_tail_{datetime.utcnow():%Y%m%d_%H%M%S}.txt"
    document = BufferedInputFile(content.encode("utf-8"), filename=filename)
    await callback.message.answer_document(document, caption=f"Последние {TAIL_LINES} строк")
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin:logs_full")
async def cb_logs_full(callback: CallbackQuery, db_user: User) -> None:
    if not _is_admin(db_user): return
    if not os.path.exists(settings.log_file_path):
        await callback.answer("Файл лога пока не создан.", show_alert=True)
        return
    await callback.message.answer_document(
        FSInputFile(settings.log_file_path), caption="Текущий файл лога",
    )
    await callback.answer()
