from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.content import ContentRepository, CONTENT_KEYS
from bot.database.repositories.settings import SettingsRepository
from bot.keyboards.admin.media import (
    media_list_kb, media_edit_kb as media_item_kb, media_cancel_kb,
    video_list_kb, VIDEO_LABELS,
)
from bot.keyboards.admin.main import back_to_admin_kb
from bot.states.admin import AdminMediaStates
from bot.handlers.admin.stats import _is_admin

router = Router()


@router.callback_query(lambda c: c.data == "admin:media")
async def cb_media(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    await state.clear()
    text = "🖼 <b>Медиа и тексты</b>\n\nВыбери раздел для редактирования:"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=media_list_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=media_list_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data and (c.data.startswith("admin:media_edit:") or c.data.startswith("admin:media_view:")))
async def cb_media_view(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    key = callback.data.rsplit(":", 1)[-1]
    if key not in CONTENT_KEYS:
        await callback.answer("❓ Неизвестный ключ.", show_alert=True)
        return
    repo = ContentRepository(session)
    item = await repo.get(key)
    text_val = (item.text if item else None) or "(не задан)"
    has_photo = bool(item and item.photo_file_id) if item else False
    msg = (
        f"📝 <b>{CONTENT_KEYS[key]}</b>\n\n"
        f"Текст:\n<code>{text_val[:1000]}</code>\n\n"
        f"Фото: {'✅ есть' if has_photo else '❌ нет'}"
    )
    try:
        await callback.message.edit_text(msg, parse_mode="HTML", reply_markup=media_item_kb(key))
    except Exception:
        await callback.message.answer(msg, parse_mode="HTML", reply_markup=media_item_kb(key))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:media_text:"))
async def cb_media_text(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    key = callback.data.rsplit(":", 1)[-1]
    await state.set_state(AdminMediaStates.enter_text)
    await state.update_data(content_key=key)
    await callback.message.answer(
        f"✏️ Отправь новый текст для <b>{CONTENT_KEYS.get(key, key)}</b>.\n\nМожно использовать HTML-разметку.",
        parse_mode="HTML",
        reply_markup=media_cancel_kb(),
    )
    await callback.answer()


@router.message(AdminMediaStates.enter_text)
async def msg_media_text(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    data = await state.get_data()
    key = data["content_key"]
    await state.clear()
    repo = ContentRepository(session)
    await repo.set_text(key, message.html_text or message.caption_html or message.text or message.caption or "")
    await message.answer(f"✅ Текст для <b>{CONTENT_KEYS.get(key, key)}</b> обновлён.", parse_mode="HTML", reply_markup=back_to_admin_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("admin:media_reset:"))
async def cb_media_reset(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    if not _is_admin(db_user): return
    key = callback.data.rsplit(":", 1)[-1]
    if key not in CONTENT_KEYS:
        await callback.answer("❓ Неизвестный ключ.", show_alert=True)
        return
    repo = ContentRepository(session)
    await repo.reset_text(key)
    await callback.answer("✅ Текст сброшен на дефолт.")
    await cb_media_view(callback, db_user, session)


@router.callback_query(lambda c: c.data and c.data.startswith("admin:media_photo:"))
async def cb_media_photo(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    key = callback.data.rsplit(":", 1)[-1]
    await state.set_state(AdminMediaStates.enter_photo)
    await state.update_data(content_key=key)
    await callback.message.answer(
        f"🖼 Отправь фото для <b>{CONTENT_KEYS.get(key, key)}</b>.\n\nИли '-' чтобы удалить текущее.",
        parse_mode="HTML",
        reply_markup=media_cancel_kb(),
    )
    await callback.answer()


@router.message(AdminMediaStates.enter_photo)
async def msg_media_photo(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    data = await state.get_data()
    key = data["content_key"]
    await state.clear()
    repo = ContentRepository(session)

    if message.text and message.text.strip() == "-":
        await repo.set_photo(key, None)
        await message.answer(f"✅ Фото для <b>{CONTENT_KEYS.get(key, key)}</b> удалено.", parse_mode="HTML", reply_markup=back_to_admin_kb())
        return

    if not message.photo:
        await message.answer("❌ Отправь фото или '-' для удаления.", reply_markup=media_cancel_kb())
        return

    file_id = message.photo[-1].file_id
    await repo.set_photo(key, file_id)
    await message.answer(f"✅ Фото для <b>{CONTENT_KEYS.get(key, key)}</b> обновлено.", parse_mode="HTML", reply_markup=back_to_admin_kb())


# ── Video upload ────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "admin:video_list")
async def cb_video_list(callback: CallbackQuery, db_user: User) -> None:
    if not _is_admin(db_user): return
    text = "🎬 <b>Видео</b>\n\nВыбери, для какого раздела загрузить видео:"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=video_list_kb())
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=video_list_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin:video_upload:"))
async def cb_video_upload(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user): return
    key = callback.data[len("admin:video_upload:"):]
    if key not in VIDEO_LABELS:
        await callback.answer("❓ Неизвестный ключ.", show_alert=True)
        return
    await state.set_state(AdminMediaStates.enter_video)
    await state.update_data(video_key=key)
    await callback.message.answer(
        f"🎬 Отправь видео для раздела <b>{VIDEO_LABELS[key]}</b>.\n\nИли '-' чтобы удалить текущее.",
        parse_mode="HTML",
        reply_markup=media_cancel_kb(),
    )
    await callback.answer()


@router.message(AdminMediaStates.enter_video)
async def msg_media_video(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not _is_admin(db_user): return
    data = await state.get_data()
    key = data["video_key"]
    await state.clear()
    repo = SettingsRepository(session)

    if message.text and message.text.strip() == "-":
        await repo.set(key, "")
        await message.answer(f"✅ Видео для <b>{VIDEO_LABELS.get(key, key)}</b> удалено.", parse_mode="HTML", reply_markup=back_to_admin_kb())
        return

    if not message.video:
        await message.answer("❌ Отправь видео-файл или '-' для удаления.", reply_markup=media_cancel_kb())
        return

    file_id = message.video.file_id
    await repo.set(key, file_id)
    await message.answer(f"✅ Видео для <b>{VIDEO_LABELS.get(key, key)}</b> обновлено.", parse_mode="HTML", reply_markup=back_to_admin_kb())


