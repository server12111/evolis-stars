from datetime import datetime
from html import escape

from aiogram import Bot, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.link_clicks import LinkButtonRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.main import main_menu_kb
from bot.keyboards.tos import tos_accept_kb
from bot.services.tos import get_tos_urls
from bot.middlewares.sponsor_wall import run_sponsor_wall_check
from bot.services.adv import send_ad
from bot.services.background import spawn_background
from bot.services.referral import (
    check_referral_reward,
    notify_referrer_joined,
    notify_user_sponsors_verified,
    reward_returning_referral,
)

router = Router()
settings = get_settings()





async def _send_main_menu(message: Message, user: User, session: AsyncSession) -> None:
    repo = ContentRepository(session)
    text = await repo.get_text("welcome")
    photo = await repo.get_photo("welcome")

    if photo:
        await message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=main_menu_kb())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb())


async def _send_tos_gate(message: Message, session: AsyncSession) -> None:
    repo = ContentRepository(session)
    text = await repo.get_text("tos")
    user_agreement_url, privacy_policy_url = await get_tos_urls(session)
    kb = tos_accept_kb(user_agreement_url, privacy_policy_url)
    await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


async def _proceed_after_tos(message: Message, db_user: User, session: AsyncSession, bot: Bot) -> None:
    """Runs once tos_accepted is guaranteed True — the sponsor wall (if any
    providers are configured) is the next gate, then the main menu."""
    if not db_user.sponsors_verified and (settings.tgrass_code or settings.botohub_key):
        if not await run_sponsor_wall_check(message, db_user, session):
            return
    db_user.sponsors_verified = True
    await session.commit()
    await notify_user_sponsors_verified(db_user, session, bot)
    await check_referral_reward(db_user, session, bot)
    await _send_main_menu(message, db_user, session)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    db_user: User,
    is_new_user: bool,
    bot: Bot,
    state: FSMContext,
    previous_last_seen_at: datetime | None = None,
) -> None:
    args = message.text.split() if message.text else []
    ref_param = args[1] if len(args) > 1 else None

    if ref_param and ref_param.startswith("lc_"):
        # Reopened via a click-tracked link button — answerCallbackQuery's
        # url can only reopen the bot itself (t.me/<bot>?start=...), never
        # an arbitrary external link, so the real destination is delivered
        # here as a normal message instead (a sent message's own buttons
        # aren't subject to that restriction).
        try:
            link_id = int(ref_param[3:])
        except ValueError:
            link_id = None
        button = await LinkButtonRepository(session).get(link_id) if link_id is not None else None
        if button and button.is_active:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="Перейти ↗", url=button.destination_url))
            await message.answer(
                f"🔗 <b>{escape(button.label)}</b>",
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
        return

    if ref_param and ref_param.startswith("ref_"):
        try:
            referrer_id = int(ref_param[4:])
            if referrer_id != db_user.user_id:
                if is_new_user:
                    user_repo = UserRepository(session)
                    referrer = await user_repo.get(referrer_id)
                    if referrer and not referrer.is_blocked:
                        db_user.referrer_id = referrer_id
                        await session.commit()
                        await notify_referrer_joined(
                            referrer_id,
                            db_user,
                            session,
                            bot,
                        )
                else:
                    await reward_returning_referral(
                        db_user,
                        referrer_id,
                        previous_last_seen_at,
                        session,
                        bot,
                    )
        except (ValueError, IndexError):
            pass

    spawn_background(
        send_ad(settings.botohub_views_key, db_user.user_id, hi=is_new_user),
        name=f"send-ad-{db_user.user_id}",
    )

    # Admins bypass ToS and the sponsor wall entirely
    if db_user.is_admin or db_user.user_id in settings.admin_id_list:
        db_user.sponsors_verified = True
        db_user.tos_accepted = True
        await session.commit()
        await _send_main_menu(message, db_user, session)
        return

    # ToS/privacy policy gate comes first, before anything else is shown —
    # only after "Принимаю" does the sponsor wall (if configured) run.
    if not db_user.tos_accepted:
        await _send_tos_gate(message, session)
        return

    await _proceed_after_tos(message, db_user, session, bot)


@router.callback_query(lambda c: c.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, db_user: User, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    spawn_background(
        send_ad(settings.botohub_views_key, db_user.user_id, hi=False),
        name=f"send-ad-{db_user.user_id}",
    )
    repo = ContentRepository(session)
    text = await repo.get_text("welcome")
    photo = await repo.get_photo("welcome")

    if photo:
        try:
            await callback.message.delete()
            await callback.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=main_menu_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb())
    else:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(lambda c: c.data == "sponsor_check")
async def cb_sponsor_check(
    callback: CallbackQuery,
    db_user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    # Old sponsor buttons can survive configuration changes.
    # IMPORTANT: If no providers are configured at all, this button should not
    # be functional — we cannot verify subscriptions without a provider.
    # Silently passing the user through would allow referral fraud.
    if not settings.tgrass_code and not settings.botohub_key:
        await callback.answer(
            "⚠️ Проверка подписок временно недоступна. Попробуйте позже.",
            show_alert=True,
        )
        return

    if not await run_sponsor_wall_check(callback, db_user, session):
        return

    # All subscribed — ToS is always accepted by this point (the sponsor
    # wall only ever starts after it), so go straight to the main menu.
    db_user.sponsors_verified = True
    await session.commit()
    await notify_user_sponsors_verified(db_user, session, bot)
    await check_referral_reward(db_user, session, bot)

    repo = ContentRepository(session)
    text = await repo.get_text("welcome")
    photo = await repo.get_photo("welcome")

    await callback.answer("✅ Проверка пройдена!")
    if photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=main_menu_kb())
    else:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb())


@router.callback_query(lambda c: c.data == "tos_accept")
async def cb_tos_accept(callback: CallbackQuery, db_user: User, session: AsyncSession, bot: Bot) -> None:
    db_user.tos_accepted = True
    await session.commit()
    await callback.answer("✅ Спасибо!")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _proceed_after_tos(callback.message, db_user, session, bot)
