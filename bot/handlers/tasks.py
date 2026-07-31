import asyncio
import logging
from html import escape

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
import time
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.task import TaskRepository
from bot.keyboards.tasks import (
    pf_task_detail_kb,
    pf_task_id,
    task_detail_kb,
    tasks_list_kb,
    fh_task_detail_kb,
)
from bot.services.piarflow import check_sponsors, get_sponsors
from bot.services.flyerhub import fh_get_tasks, fh_check_task
from bot.services.referral import check_referral_reward
from bot.services.telegram_chat import is_subscribed, telegram_chat_id

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)


def _pf_chat_id() -> int:
    try:
        if settings.admin_channel_id:
            return int(settings.admin_channel_id)
    except (ValueError, TypeError):
        pass
    return 0


async def _find_next_pf_task(s_repo: SettingsRepository, db_user: User, pf_tasks: list, skip_link: str = ""):
    """Returns (idx, sponsor) for next uncompleted PiarFlow task, or (None, None).
    skip_link: temporarily skip tasks whose link starts with this prefix (used for Skip button)."""
    for idx, sp in enumerate(pf_tasks):
        link = sp.get("link", "")
        if not link:
            continue
        link_id = pf_task_id(link)
        if skip_link and link.startswith(skip_link[:30]):
            continue
        done = await s_repo.get(
            f"pf_done:{db_user.user_id}:{link_id}",
            "",
        )
        skipped = await s_repo.get(
            f"pf_skipped:{db_user.user_id}:{link_id}",
            "",
        )
        is_skipped = False
        if skipped.isdigit() and int(time.time()) - int(skipped) < 900:
            is_skipped = True
            
        legacy_done = await s_repo.get(
            f"pf_done:{db_user.user_id}:{link[:30]}",
            "",
        )
        if done != "1" and legacy_done != "1" and not is_skipped:
            return idx, sp
    return None, None


@router.callback_query(lambda c: c.data == "menu:tasks")
async def cb_tasks_menu(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return

    await callback.answer()
    await _show_next_task(callback, db_user, session)


async def _show_next_task(callback: CallbackQuery, db_user: User, session: AsyncSession, current_task_id: int | None = None) -> None:
    """Show next uncompleted task, or task list if none left."""
    task_repo = TaskRepository(session)
    all_tasks = await task_repo.all_active()
    completed_ids = await task_repo.completed_ids(db_user.user_id)
    uncompleted = [t for t in all_tasks if t.id not in completed_ids and t.id != current_task_id]

    if not uncompleted:
        pf_configured = bool(settings.piarflow_key)
        fh_configured = bool(settings.flyerhub_key)
        if pf_configured and fh_configured:
            # Alternate which provider is tried first so FlyerHub tasks
            # actually surface instead of waiting for PiarFlow's whole
            # batch (up to piarflow_max_sponsors) to be exhausted first.
            if db_user.tasks_completed_count % 2 == 1:
                await _show_fh_task(callback, db_user, session)
            else:
                await _show_pf_task(callback, db_user, session)
            return
        if pf_configured:
            await _show_pf_task(callback, db_user, session)
            return
        if fh_configured:
            await _show_fh_task(callback, db_user, session)
            return
        s_repo = SettingsRepository(session)
        tasks_reward = await s_repo.get_float("tasks_reward", 0.3)
        text = (
            f"📋 <b>Задания</b>\n\n"
            f"Выполняй задания и получай <b>{tasks_reward:.1f} ⭐</b> за каждое!\n"
            f"✅ Выполнено: <b>{db_user.tasks_completed_count}</b>\n\n"
            f"🎉 Все доступные задания выполнены!"
        )
        kb = tasks_list_kb(all_tasks, completed_ids)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    task = uncompleted[0]
    text = (
        f"📌 <b>{task.title}</b>\n\n"
        f"{task.description}\n\n"
        f"💰 Награда: <b>{float(task.reward):.1f} ⭐</b>"
    )
    kb = task_detail_kb(task.id, task.url)
    if task.photo_file_id:
        try:
            await callback.message.answer_photo(
                task.photo_file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("Failed to show photo for task %s: %s", task.id, exc)
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=kb,
            )
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


@router.callback_query(lambda c: c.data and c.data.startswith("task:view:"))
async def cb_task_view(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    try:
        task_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    task_repo = TaskRepository(session)
    task = await task_repo.get(task_id)
    if not task:
        await callback.answer("Задание не найдено.", show_alert=True)
        return

    completed = await task_repo.is_completed(task_id, db_user.user_id)
    status = "✅ Выполнено" if completed else "⏳ Не выполнено"
    text = (
        f"📌 <b>{task.title}</b>\n\n"
        f"{task.description}\n\n"
        f"💰 Награда: <b>{float(task.reward):.1f} ⭐</b>\n"
        f"Статус: {status}"
    )
    kb = task_detail_kb(task_id, task.url)
    if task.photo_file_id:
        try:
            await callback.message.answer_photo(
                task.photo_file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("Failed to show photo for task %s: %s", task.id, exc)
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=kb,
            )
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


@router.callback_query(lambda c: c.data and c.data.startswith("task:check:"))
async def cb_task_check(callback: CallbackQuery, db_user: User, session: AsyncSession, bot: Bot) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    try:
        task_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    task_repo = TaskRepository(session)
    task = await task_repo.get(task_id)
    if not task:
        await callback.answer("Задание не найдено.", show_alert=True)
        return

    already = await task_repo.is_completed(task_id, db_user.user_id)
    if already:
        await callback.answer("✅ Задание уже выполнено!", show_alert=True)
        return

    if task.task_type == "channel_sub" and task.url:
        chat_id = telegram_chat_id(task.url)
        if chat_id is None:
            await callback.answer(
                "⚠️ Канал задания настроен неверно. Сообщите администратору.",
                show_alert=True,
            )
            return
        try:
            member = await bot.get_chat_member(chat_id, db_user.user_id)
            if not is_subscribed(member):
                await callback.answer("❌ Вы не подписаны на канал. Подпишитесь и попробуйте ещё раз.", show_alert=True)
                return
        except Exception:
            await callback.answer(
                "⚠️ Не удалось проверить подписку. Попробуйте ещё раз позже.",
                show_alert=True,
            )
            return

    marked = await task_repo.mark_completed(task_id, db_user.user_id)
    if marked:
        reward = float(task.reward)
        db_user.stars_balance = round(float(db_user.stars_balance) + reward, 2)
        db_user.tasks_completed_count += 1
        await session.commit()
        await callback.answer(f"✅ Задание выполнено! +{reward:.1f} ⭐", show_alert=True)
        await check_referral_reward(db_user, session, bot)
        await _show_next_task(callback, db_user, session, current_task_id=task_id)
    else:
        await callback.answer("⚠️ Не удалось отметить задание.", show_alert=True)


@router.callback_query(lambda c: c.data and c.data.startswith("task:skip:"))
async def cb_task_skip(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    try:
        task_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    await callback.answer()
    await _show_next_task(callback, db_user, session, current_task_id=task_id)


@router.callback_query(lambda c: c.data == "task:already_done")
async def cb_task_already_done(callback: CallbackQuery) -> None:
    await callback.answer("✅ Это задание уже выполнено!", show_alert=True)


# ── PiarFlow task handlers (one-by-one flow) ───────────────────────────────

async def _show_pf_unavailable(callback: CallbackQuery) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔄 Попробовать ещё раз",
            callback_data="menu:tasks",
        )
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")
    )
    text = (
        "⚠️ Сервис заданий временно не ответил.\n\n"
        "Награда не потеряна. Попробуй ещё раз через несколько секунд."
    )
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )


async def _show_pf_task(
    callback: CallbackQuery,
    db_user: User,
    session: AsyncSession,
    skip_link: str = "",
    pf_tasks: list[dict] | None = None,
    retry_if_empty: bool = False,
    tried_other: bool = False,
) -> None:
    """Shows next uncompleted PiarFlow task, or returns to tasks menu if none left."""
    s_repo = SettingsRepository(session)
    tasks_reward = await s_repo.get_float("tasks_reward", 0.3)
    max_sponsors = await s_repo.get_int("piarflow_max_sponsors", 100)
    if pf_tasks is None:
        pf_tasks = await get_sponsors(
            settings.piarflow_key,
            db_user.user_id,
            _pf_chat_id(),
            max_sponsors,
        )
        if pf_tasks is None:
            await _show_pf_unavailable(callback)
            return

    idx, sponsor = await _find_next_pf_task(s_repo, db_user, pf_tasks, skip_link=skip_link)

    if sponsor is None and retry_if_empty:
        # PiarFlow can briefly return an empty list immediately after it
        # confirms a subscription. Retry before claiming that everything is
        # complete, so the next task is displayed without a manual refresh.
        for _ in range(2):
            await asyncio.sleep(0.35)
            refreshed = await get_sponsors(
                settings.piarflow_key,
                db_user.user_id,
                _pf_chat_id(),
                max_sponsors,
            )
            if refreshed is None:
                await _show_pf_unavailable(callback)
                return
            idx, sponsor = await _find_next_pf_task(
                s_repo,
                db_user,
                refreshed,
                skip_link=skip_link,
            )
            if sponsor is not None:
                pf_tasks = refreshed
                break

    if sponsor is None:
        if not tried_other and settings.flyerhub_key:
            await _show_fh_task(callback, db_user, session, tried_other=True)
            return

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="menu:tasks"))
        builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
        text = (
            f"📋 <b>Задания</b>\n\n"
            f"✅ Выполнено: <b>{db_user.tasks_completed_count}</b>\n\n"
            "🎉 Все задания выполнены! Загляни позже."
        )
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return
            await callback.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
        return

    link = sponsor.get("link", "")
    link_id = pf_task_id(link)
    await s_repo.set(f"pf_link:{db_user.user_id}:{link_id}", link)
    name = escape(str(sponsor.get("name", "Канал")))
    text = (
        f"✨ <b>Новое задание!</b> ✨\n\n"
        f"• Подпишись на канал <b>{name}</b>\n\n"
        f"Награда: <b>{tasks_reward:.1f} ⭐</b>\n\n"
        f"После выполнения жми «✅ Проверить выполнение»"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=pf_task_detail_kb(link))
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
        await callback.message.answer(text, parse_mode="HTML", reply_markup=pf_task_detail_kb(link))


@router.callback_query(lambda c: c.data == "pf_task:start")
async def cb_pf_task_start(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    if not settings.piarflow_key:
        await callback.answer("Сервис недоступен.", show_alert=True)
        return
    await callback.answer()
    await _show_pf_task(callback, db_user, session)


@router.callback_query(lambda c: c.data and c.data.startswith("pf_task:skip:"))
async def cb_pf_task_skip(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    link_key = callback.data[len("pf_task:skip:"):]
    if not settings.piarflow_key:
        await callback.answer()
        return
    s_repo = SettingsRepository(session)
    await s_repo.set(f"pf_skipped:{db_user.user_id}:{link_key}", str(int(time.time())))
    await callback.answer()
    await _show_pf_task(callback, db_user, session)


@router.callback_query(lambda c: c.data and c.data.startswith("pf_task:check:"))
async def cb_pf_task_check(callback: CallbackQuery, db_user: User, session: AsyncSession, bot: Bot) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    link_key = callback.data[len("pf_task:check:"):]

    # Old buttons stored numeric idx — ask user to reopen the task
    if not link_key or link_key.isdigit():
        await callback.answer("Нажмите «Задания» снова для обновления.", show_alert=True)
        return

    if not settings.piarflow_key:
        await callback.answer("Сервис недоступен.", show_alert=True)
        return

    s_repo = SettingsRepository(session)
    max_sponsors = await s_repo.get_int("piarflow_max_sponsors", 100)
    tasks_reward = await s_repo.get_float("tasks_reward", 0.3)

    key = f"pf_done:{db_user.user_id}:{link_key}"
    if await s_repo.get(key, "") == "1":
        await callback.answer()
        await _show_pf_task(callback, db_user, session)
        return

    # Показываем индикатор загрузки пока идёт проверка API
    try:
        await callback.message.edit_text("⏳ Проверяем подписку...", parse_mode="HTML")
    except Exception:
        pass

    full_link = await s_repo.get(
        f"pf_link:{db_user.user_id}:{link_key}",
        "",
    )
    pf_tasks: list[dict] | None = None
    if not full_link:
        # Legacy prefix-based buttons can only be verified while their task is
        # still returned by PiarFlow. Never reward an unknown/partial link.
        pf_tasks = await get_sponsors(
            settings.piarflow_key,
            db_user.user_id,
            _pf_chat_id(),
            max_sponsors,
        )
        if pf_tasks is None:
            await _show_pf_unavailable(callback)
            return
        for sponsor in pf_tasks:
            candidate = str(sponsor.get("link", ""))
            if candidate.startswith(link_key[:45]):
                full_link = candidate
                break
        if not full_link:
            await callback.message.edit_text(
                "⚠️ Кнопка задания устарела. Откройте раздел «Задания» заново.",
                parse_mode="HTML",
            )
            return
        await s_repo.set(
            f"pf_link:{db_user.user_id}:{pf_task_id(full_link)}",
            full_link,
        )

    subscribed = await check_sponsors(
        settings.piarflow_key,
        db_user.user_id,
        [full_link],
    )

    if not subscribed:
        await callback.answer("❌ Вы не подписаны. Подпишитесь и нажмите «Проверить».", show_alert=True)
        try:
            await callback.message.edit_text(
                "❌ Вы не подписаны на канал. Подпишитесь и нажмите «Проверить».",
                parse_mode="HTML",
                reply_markup=pf_task_detail_kb(full_link),
            )
        except Exception:
            pass
        return

    await s_repo.stage(key, "1")
    db_user.stars_balance = round(float(db_user.stars_balance) + tasks_reward, 2)
    db_user.tasks_completed_count += 1
    await session.commit()
    await check_referral_reward(db_user, session, bot)
    
    try:
        await callback.message.edit_text("✅ Подписка подтверждена!\nЗагружаем следующее задание...", parse_mode="HTML")
    except Exception:
        pass

    # Показываем флешку «Задание выполнено» сверху экрана
    try:
        await callback.answer("✅ Задание выполнено!", show_alert=True)
    except Exception:
        pass
    if pf_tasks is None:
        pf_tasks = await get_sponsors(
            settings.piarflow_key,
            db_user.user_id,
            _pf_chat_id(),
            max_sponsors,
        )
        if pf_tasks is None:
            await _show_pf_unavailable(callback)
            return
    await _show_pf_task(
        callback,
        db_user,
        session,
        pf_tasks=pf_tasks,
        retry_if_empty=True,
    )


async def _show_fh_task(
    callback: CallbackQuery,
    db_user: User,
    session: AsyncSession,
    skip_signature: str = "",
    fh_tasks: list[dict] | None = None,
    tried_other: bool = False,
) -> None:
    s_repo = SettingsRepository(session)
    tasks_reward = await s_repo.get_float("tasks_reward", 0.3)
    
    if fh_tasks is None:
        fh_tasks = await fh_get_tasks(
            settings.flyerhub_key,
            db_user.user_id,
            _pf_chat_id(),
        )
        if fh_tasks is None:
            await _show_pf_unavailable(callback)
            return

    sponsor = None
    for task in fh_tasks:
        sig = str(task.get("signature", ""))
        if not sig:
            continue
        if sig == skip_signature:
            continue
        key = f"fh_done:{db_user.user_id}:{sig}"
        if await s_repo.get(key, "") == "1":
            continue
            
        skip_key = f"fh_skipped:{db_user.user_id}:{sig}"
        skipped = await s_repo.get(skip_key, "")
        if skipped.isdigit() and int(time.time()) - int(skipped) < 900:
            continue
            
        sponsor = task
        break

    if sponsor is None:
        if not tried_other and settings.piarflow_key:
            await _show_pf_task(callback, db_user, session, tried_other=True)
            return

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="menu:tasks"))
        builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"))
        text = (
            f"📋 <b>Задания</b>\n\n"
            f"✅ Выполнено: <b>{db_user.tasks_completed_count}</b>\n\n"
            "🎉 Все задания выполнены! Загляни позже."
        )
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return
            await callback.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
        return

    sig = str(sponsor.get("signature", ""))
    links = sponsor.get("links", [])
    link = links[0] if links else ""
    name = escape(str(sponsor.get("name", "Задание")))
    text = (
        f"✨ <b>Новое задание!</b> ✨\n\n"
        f"• Выполни задание: <b>{name}</b>\n\n"
        f"Награда: <b>{tasks_reward:.1f} ⭐</b>\n\n"
        f"После выполнения жми «✅ Проверить выполнение»"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=fh_task_detail_kb(link, sig))
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
        await callback.message.answer(text, parse_mode="HTML", reply_markup=fh_task_detail_kb(link, sig))


@router.callback_query(lambda c: c.data and c.data.startswith("fh_task:skip:"))
async def cb_fh_task_skip(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    signature = callback.data[len("fh_task:skip:"):]
    if not settings.flyerhub_key:
        await callback.answer()
        return
    
    s_repo = SettingsRepository(session)
    await s_repo.set(f"fh_skipped:{db_user.user_id}:{signature}", str(int(time.time())))
    await callback.answer()
    await _show_fh_task(callback, db_user, session)


@router.callback_query(lambda c: c.data and c.data.startswith("fh_task:check:"))
async def cb_fh_task_check(callback: CallbackQuery, db_user: User, session: AsyncSession, bot: Bot) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    signature = callback.data[len("fh_task:check:"):]

    if not settings.flyerhub_key:
        await callback.answer("Сервис недоступен.", show_alert=True)
        return

    s_repo = SettingsRepository(session)
    tasks_reward = await s_repo.get_float("tasks_reward", 0.3)

    key = f"fh_done:{db_user.user_id}:{signature}"
    if await s_repo.get(key, "") == "1":
        await callback.answer()
        await _show_fh_task(callback, db_user, session)
        return

    try:
        await callback.message.edit_text("⏳ Проверяем выполнение...", parse_mode="HTML")
    except Exception:
        pass

    status = await fh_check_task(settings.flyerhub_key, signature)
    if status is None:
        await _show_pf_unavailable(callback)
        return

    if status in ("complete", "waiting", "not_counted"):
        await s_repo.set(key, "1")
        db_user.stars_balance = round(float(db_user.stars_balance) + tasks_reward, 2)
        db_user.tasks_completed_count += 1
        await session.commit()
        await check_referral_reward(db_user, session, bot)
        
        try:
            await callback.answer("✅ Задание выполнено!", show_alert=True)
        except Exception:
            pass
            
        try:
            await callback.message.edit_text("✅ Задание подтверждено! Загружаем следующее...", parse_mode="HTML")
        except Exception:
            pass

        await _show_fh_task(callback, db_user, session)
    elif status == "unavailable":
        try:
            await callback.answer("Задание больше не актуально.", show_alert=True)
        except Exception:
            pass
        await s_repo.set(key, "1")  # hide it
        await _show_fh_task(callback, db_user, session)
    else:
        try:
            await callback.answer("❌ Задание ещё не выполнено. Попробуйте снова.", show_alert=True)
        except Exception:
            pass
        await _show_fh_task(callback, db_user, session)
