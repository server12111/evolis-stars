import math
import logging

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.task import TaskRepository
from bot.keyboards.admin.tasks import tasks_list_kb, task_view_kb, task_cancel_kb
from bot.keyboards.admin.main import back_to_admin_kb
from bot.states.admin import AdminTaskStates
from bot.handlers.admin.stats import _is_admin

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(lambda c: c.data == "admin:tasks")
async def cb_tasks(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    repo = TaskRepository(session)
    tasks = await repo.all_tasks()
    text = f"📋 <b>Задания</b>\n\nВсего: <b>{len(tasks)}</b>"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=tasks_list_kb(tasks))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=tasks_list_kb(tasks))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:task_view:"))
async def cb_task_view(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    tid = int(callback.data.split(":")[2])
    repo = TaskRepository(session)
    task = await repo.get(tid)
    if not task:
        await callback.answer("❌ Задание не найдено.", show_alert=True)
        return
    text = (
        f"📌 <b>{task.title}</b>\n\n{task.description}\n\n"
        f"🔗 URL: {task.url or 'нет'}\n"
        f"💰 Награда: <b>{float(task.reward):.1f} ⭐</b>\n"
        f"👥 Выполнено: <b>{task.completions_count}</b>\n"
        f"📊 Лимит: <b>{task.max_completions or '∞'}</b>"
    )
    kb = task_view_kb(tid, task.is_active)
    if task.photo_file_id:
        try:
            await callback.message.answer_photo(
                task.photo_file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("Failed to show photo for admin task %s: %s", tid, exc)
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
        else:
            try:
                await callback.message.delete()
            except Exception:
                pass
    else:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:task_toggle:"))
async def cb_task_toggle(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    tid = int(callback.data.split(":")[2])
    repo = TaskRepository(session)
    task = await repo.get(tid)
    if not task:
        await callback.answer("❌ Не найдено.", show_alert=True)
        return
    task.is_active = not task.is_active
    await session.commit()
    status = "включено" if task.is_active else "отключено"
    await callback.answer(f"{'✅' if task.is_active else '❌'} Задание {status}")
    try:
        await callback.message.edit_reply_markup(reply_markup=task_view_kb(tid, task.is_active))
    except Exception:
        pass


@router.callback_query(lambda c: c.data and c.data.startswith("admin:task_del:"))
async def cb_task_del(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    tid = int(callback.data.split(":")[2])
    repo = TaskRepository(session)
    deleted = await repo.delete(tid)
    await callback.answer("✅ Задание удалено" if deleted else "❌ Не найдено", show_alert=True)
    if deleted:
        tasks = await repo.all_tasks()
        try:
            await callback.message.edit_text(f"📋 <b>Задания</b>\n\nВсего: <b>{len(tasks)}</b>", parse_mode="HTML", reply_markup=tasks_list_kb(tasks))
        except Exception:
            pass


@router.callback_query(lambda c: c.data == "admin:task_new")
async def cb_task_new(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.set_state(AdminTaskStates.enter_title)
    await callback.message.answer("📌 Введи название задания:", reply_markup=task_cancel_kb())
    await callback.answer()


@router.message(AdminTaskStates.enter_title)
async def msg_task_title(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    await state.update_data(title=message.text or "")
    await state.set_state(AdminTaskStates.enter_description)
    await message.answer("📝 Введи описание (или отправь '-' если не нужно):", reply_markup=task_cancel_kb())


@router.message(AdminTaskStates.enter_description)
async def msg_task_desc(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    desc = message.html_text or message.text or ""
    if desc == "-": desc = ""
    await state.update_data(description=desc)
    await state.set_state(AdminTaskStates.enter_url)
    await message.answer("🔗 Введи URL задания (или '-' если нет):", reply_markup=task_cancel_kb())


@router.message(AdminTaskStates.enter_url)
async def msg_task_url(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    url = message.text or ""
    if url == "-": url = ""
    await state.update_data(url=url)
    await state.set_state(AdminTaskStates.enter_reward)
    await message.answer("💰 Введи награду (кол-во ⭐):", reply_markup=task_cancel_kb())


@router.message(AdminTaskStates.enter_reward)
async def msg_task_reward(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user): return
    try:
        reward = float(message.text.strip().replace(",", "."))
        if not math.isfinite(reward) or reward <= 0: raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введи положительное число:", reply_markup=task_cancel_kb())
        return
    await state.update_data(reward=reward)
    await state.set_state(AdminTaskStates.enter_photo)
    await message.answer("🖼 Отправь фото для задания (или '-' если без фото):", reply_markup=task_cancel_kb())


@router.message(AdminTaskStates.enter_photo)
async def msg_task_photo(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    photo_file_id: str | None = None
    if message.photo:
        photo_file_id = message.photo[-1].file_id
    elif not (message.text and message.text.strip() == "-"):
        await message.answer("❌ Отправь фото или '-' для продолжения без фото:", reply_markup=task_cancel_kb())
        return

    data = await state.get_data()
    await state.clear()
    repo = TaskRepository(session)
    task = await repo.create(
        title=data["title"],
        description=data["description"],
        url=data["url"],
        task_type="external_url" if data["url"] else "manual",
        reward=data["reward"],
        photo_file_id=photo_file_id,
    )
    await message.answer(
        f"✅ <b>Задание создано!</b>\n\n<b>{task.title}</b>\nНаграда: {float(task.reward):.1f} ⭐",
        parse_mode="HTML",
        reply_markup=back_to_admin_kb(),
    )
