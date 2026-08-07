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
from bot.database.repositories.content import DEFAULT_TEXTS, ContentRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.task import TaskRepository
from bot.keyboards.tasks import (
    pf_task_detail_kb,
    pf_task_id,
    task_detail_kb,
    tasks_list_kb,
    fh_task_detail_kb,
    fh_webapp_kb,
    linkni_kb,
)
from bot.services.piarflow import check_sponsors, get_sponsors
from bot.services.flyerhub import fh_get_tasks, fh_check_task, fh_get_completed_tasks
from bot.services.linkni import check_linkni_subscription, linkni_link
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


async def _show_tasks_exhausted_screen(callback: CallbackQuery, db_user: User) -> None:
    """Final fallback — nothing left from admin tasks, PiarFlow, FlyerHub, or
    linkni, or one of them failed to respond. Shown as "come back later"
    rather than an error so a transient provider outage doesn't read as a
    bug to the user."""
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


async def _show_linkni_task(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    text = (
        "✨ <b>Новое задание!</b> ✨\n\n"
        "Перейди по кнопке ниже и выполни задание, затем вернись сюда и "
        "нажми «✅ Проверить выполнение»."
    )
    kb = linkni_kb(linkni_link(settings.linkni_code))
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


async def _try_show_linkni(callback: CallbackQuery, db_user: User, session: AsyncSession) -> bool:
    """linkni is the last-resort task source — only offered once every other
    source (admin tasks, PiarFlow, FlyerHub) is exhausted. Returns True if
    it was shown (caller should stop rendering anything else)."""
    if not settings.linkni_code:
        return False
    s_repo = SettingsRepository(session)
    if await s_repo.get(f"linkni_done:{db_user.user_id}", "") == "1":
        return False
    await _show_linkni_task(callback, db_user, session)
    return True


@router.callback_query(lambda c: c.data == "linkni:check")
async def cb_linkni_check(callback: CallbackQuery, db_user: User, session: AsyncSession, bot: Bot) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    if not settings.linkni_code:
        await callback.answer("⚠️ Сервис недоступен.", show_alert=True)
        return

    s_repo = SettingsRepository(session)
    key = f"linkni_done:{db_user.user_id}"
    if await s_repo.get(key, "") == "1":
        await callback.answer()
        await _show_tasks_exhausted_screen(callback, db_user)
        return

    status = await check_linkni_subscription(settings.linkni_code, db_user.user_id)
    if status == "subscribed":
        tasks_reward = await s_repo.get_float("tasks_reward", 0.3)
        await s_repo.set(key, "1")
        db_user.stars_balance = round(float(db_user.stars_balance) + tasks_reward, 2)
        db_user.tasks_completed_count += 1
        await session.commit()
        await check_referral_reward(db_user, session, bot)
        await callback.answer(f"✅ Задание выполнено! +{tasks_reward:.1f} RP⭐️", show_alert=True)
        await _show_tasks_exhausted_screen(callback, db_user)
    elif status == "no_sponsors":
        await s_repo.set(key, "1")
        await callback.answer("❌ Сейчас нет доступных спонсоров.", show_alert=True)
        await _show_tasks_exhausted_screen(callback, db_user)
    elif status == "not_subscribed":
        await callback.answer("❌ Вы не подписались. Перейдите по ссылке и попробуйте снова.", show_alert=True)
    else:
        await callback.answer("⏳ Ещё не зафиксировали переход. Попробуйте через минуту.", show_alert=True)


@router.callback_query(lambda c: c.data == "linkni:skip")
async def cb_linkni_skip(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    await callback.answer()
    await _show_tasks_exhausted_screen(callback, db_user)


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
    s_repo = SettingsRepository(session)
    all_tasks = await task_repo.all_active()
    completed_ids = await task_repo.completed_ids(db_user.user_id)
    uncompleted = []
    for t in all_tasks:
        if t.id in completed_ids or t.id == current_task_id:
            continue
        # Same persisted "recently skipped" pattern (15-min TTL) as the
        # PiarFlow/FlyerHub skip buttons below -- without it, "skip" only
        # ever excluded the ONE task just pressed for that single render,
        # not remembered across calls. With exactly 2 admin tasks that let
        # "skip" cycle A -> B -> A -> B forever instead of ever reaching
        # "nothing left" and falling through to PiarFlow/FlyerHub.
        skipped = await s_repo.get(f"task_skipped:{db_user.user_id}:{t.id}", "")
        if skipped.isdigit() and int(time.time()) - int(skipped) < 900:
            continue
        uncompleted.append(t)

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
        if await _try_show_linkni(callback, db_user, session):
            return
        tasks_reward = await s_repo.get_float("tasks_reward", 0.3)
        reward_str = f"{tasks_reward:.1f}"
        template = await ContentRepository(session).get_text("tasks")
        try:
            intro = template.format(tasks_reward=reward_str) if "{" in template else template
        except (KeyError, ValueError, IndexError):
            intro = DEFAULT_TEXTS["tasks"].format(tasks_reward=reward_str)
        text = (
            f"{intro}\n"
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
        f"💰 Награда: <b>{float(task.reward):.1f} RP⭐️</b>"
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
        await callback.answer("❌ Задание не найдено.", show_alert=True)
        return

    completed = await task_repo.is_completed(task_id, db_user.user_id)
    status = "✅ Выполнено" if completed else "⏳ Не выполнено"
    text = (
        f"📌 <b>{task.title}</b>\n\n"
        f"{task.description}\n\n"
        f"💰 Награда: <b>{float(task.reward):.1f} RP⭐️</b>\n"
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
        await callback.answer("❌ Задание не найдено.", show_alert=True)
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
        await callback.answer(f"✅ Задание выполнено! +{reward:.1f} RP⭐️", show_alert=True)
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
    await SettingsRepository(session).set(
        f"task_skipped:{db_user.user_id}:{task_id}", str(int(time.time())),
    )
    await callback.answer()
    await _show_next_task(callback, db_user, session, current_task_id=task_id)


@router.callback_query(lambda c: c.data == "task:already_done")
async def cb_task_already_done(callback: CallbackQuery) -> None:
    await callback.answer("✅ Это задание уже выполнено!", show_alert=True)


# ── PiarFlow task handlers (one-by-one flow) ───────────────────────────────


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
            await _show_tasks_exhausted_screen(callback, db_user)
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
                await _show_tasks_exhausted_screen(callback, db_user)
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
        # In webapp mode FlyerHub is just a static "go to the app" screen,
        # not a discrete list to exhaust — cross-falling-back there again
        # (the user already sees it on its own alternation turn) only adds
        # an extra manual skip between PiarFlow running dry and reaching
        # linkni. Go straight to linkni in that case.
        if not tried_other and settings.flyerhub_key and not settings.flyerhub_webapp_url:
            await _show_fh_task(callback, db_user, session, tried_other=True)
            return
        if await _try_show_linkni(callback, db_user, session):
            return
        await _show_tasks_exhausted_screen(callback, db_user)
        return

    link = sponsor.get("link", "")
    link_id = pf_task_id(link)
    await s_repo.set(f"pf_link:{db_user.user_id}:{link_id}", link)
    name = escape(str(sponsor.get("name", "Канал")))
    text = (
        f"✨ <b>Новое задание!</b> ✨\n\n"
        f"• Подпишись на канал <b>{name}</b>\n\n"
        f"Награда: <b>{tasks_reward:.1f} RP⭐️</b>\n\n"
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
        await callback.answer("⚠️ Сервис недоступен.", show_alert=True)
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
        await callback.answer("🔄 Нажмите «Задания» снова для обновления.", show_alert=True)
        return

    if not settings.piarflow_key:
        await callback.answer("⚠️ Сервис недоступен.", show_alert=True)
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
            await _show_tasks_exhausted_screen(callback, db_user)
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
            await _show_tasks_exhausted_screen(callback, db_user)
            return
    await _show_pf_task(
        callback,
        db_user,
        session,
        pf_tasks=pf_tasks,
        retry_if_empty=True,
    )


async def _show_fh_webapp(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    """FlyerHub bot keys registered as a "webapp" type reject /get_tasks and
    /check_task outright ("Prohibited method for a bot type") — tasks are
    discovered and completed entirely inside FlyerHub's own Telegram Mini
    App, so we just deep-link there and poll /get_completed_tasks to learn
    what to pay for."""
    result = await fh_get_completed_tasks(settings.flyerhub_key, db_user.user_id)
    if result is None:
        await _show_tasks_exhausted_screen(callback, db_user)
        return

    # count_all_tasks only reflects what FlyerHub has already assigned to
    # this user — it's 0 for anyone who hasn't opened the Mini App yet, so
    # it can't be used to decide "nothing to do"; always offer the link.
    count_line = (
        f"Доступно заданий: <b>{result['count_all_tasks']}</b>\n\n"
        if result["count_all_tasks"] > 0
        else ""
    )
    text = (
        f"✨ <b>Новое задание!</b> ✨\n\n"
        f"{count_line}"
        "Перейди по кнопке ниже и выполни задание, затем вернись сюда и "
        "нажми «✅ Проверить выполнение»."
    )
    kb = fh_webapp_kb(settings.flyerhub_webapp_url)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(lambda c: c.data == "fh_webapp:check")
async def cb_fh_webapp_check(callback: CallbackQuery, db_user: User, session: AsyncSession, bot: Bot) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    if not settings.flyerhub_key:
        await callback.answer("⚠️ Сервис недоступен.", show_alert=True)
        return

    result = await fh_get_completed_tasks(settings.flyerhub_key, db_user.user_id)
    if result is None:
        await _show_tasks_exhausted_screen(callback, db_user)
        return

    s_repo = SettingsRepository(session)
    tasks_reward = await s_repo.get_float("tasks_reward", 0.3)
    new_signatures = []
    for item in result["completed_tasks"]:
        signature = str(item.get("signature", ""))
        if not signature:
            continue
        key = f"fh_done:{db_user.user_id}:{signature}"
        if await s_repo.get(key, "") == "1":
            continue
        await s_repo.set(key, "1")
        new_signatures.append(signature)

    if new_signatures:
        reward = round(tasks_reward * len(new_signatures), 2)
        db_user.stars_balance = round(float(db_user.stars_balance) + reward, 2)
        db_user.tasks_completed_count += len(new_signatures)
        await session.commit()
        await check_referral_reward(db_user, session, bot)
        await callback.answer(
            f"✅ Засчитано новых заданий: {len(new_signatures)} (+{reward:.2f} RP⭐️)",
            show_alert=True,
        )
    else:
        await callback.answer("❌ Новых выполненных заданий не найдено.", show_alert=True)

    await _show_fh_webapp(callback, db_user, session)


@router.callback_query(lambda c: c.data == "fh_webapp:skip")
async def cb_fh_webapp_skip(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not await SettingsRepository(session).get_bool("tasks_enabled", True):
        await callback.answer("📋 Задания временно недоступны.", show_alert=True)
        return
    await callback.answer()
    if settings.piarflow_key:
        await _show_pf_task(callback, db_user, session, tried_other=True)
        return
    if await _try_show_linkni(callback, db_user, session):
        return
    await _show_tasks_exhausted_screen(callback, db_user)


async def _show_fh_task(
    callback: CallbackQuery,
    db_user: User,
    session: AsyncSession,
    skip_signature: str = "",
    fh_tasks: list[dict] | None = None,
    tried_other: bool = False,
) -> None:
    if settings.flyerhub_webapp_url:
        await _show_fh_webapp(callback, db_user, session)
        return

    s_repo = SettingsRepository(session)
    tasks_reward = await s_repo.get_float("tasks_reward", 0.3)

    if fh_tasks is None:
        fh_tasks = await fh_get_tasks(
            settings.flyerhub_key,
            db_user.user_id,
            db_user.user_id,
        )
        if fh_tasks is None:
            await _show_tasks_exhausted_screen(callback, db_user)
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
        if await _try_show_linkni(callback, db_user, session):
            return
        await _show_tasks_exhausted_screen(callback, db_user)
        return

    sig = str(sponsor.get("signature", ""))
    links = sponsor.get("links", [])
    link = links[0] if links else ""
    name = escape(str(sponsor.get("name", "Задание")))
    text = (
        f"✨ <b>Новое задание!</b> ✨\n\n"
        f"• Выполни задание: <b>{name}</b>\n\n"
        f"Награда: <b>{tasks_reward:.1f} RP⭐️</b>\n\n"
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
        await callback.answer("⚠️ Сервис недоступен.", show_alert=True)
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
        await _show_tasks_exhausted_screen(callback, db_user)
        return

    if status == "complete":
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
    elif status == "waiting":
        # A distinct anti-fraud hold, not a confirmed completion — must
        # NOT pay or set fh_done, or the hold is defeated by just tapping
        # "check" once instead of actually finishing the sponsor's task.
        try:
            await callback.answer("⏳ Выполнение проверяется, попробуйте через минуту.", show_alert=True)
        except Exception:
            pass
        await _show_fh_task(callback, db_user, session)
    elif status == "unavailable":
        try:
            await callback.answer("⏳ Задание больше не актуально.", show_alert=True)
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
