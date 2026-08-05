import logging
from datetime import datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.chat import ChatRepository
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
    sponsors_word,
)
from bot.services.sponsor_waves import (
    _current_items,
    skip_current_wave,
    sponsor_wave_markup,
    sponsor_wave_text,
)

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)





async def _send_main_menu(message: Message, user: User, session: AsyncSession) -> None:
    repo = ContentRepository(session)
    text = await repo.get_text("welcome")
    photo = await repo.get_photo("welcome")
    has_chats = await ChatRepository(session).has_owned_chats(user.user_id)

    if photo:
        await message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=main_menu_kb(has_chats))
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb(has_chats))


async def _send_tos_gate(message: Message, session: AsyncSession) -> None:
    repo = ContentRepository(session)
    text = await repo.get_text("tos")
    user_agreement_url, privacy_policy_url = await get_tos_urls(session)
    kb = tos_accept_kb(user_agreement_url, privacy_policy_url)
    await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


async def _mark_sponsors_verified_and_notify(db_user: User, session: AsyncSession, bot: Bot) -> None:
    """Shared tail for every path that can complete the sponsor wall (the
    "Я подписался" re-check and the paid skip) — only fires the one-time
    notify/reward hooks on the genuine transition into verified."""
    was_verified = db_user.sponsors_verified
    db_user.sponsors_verified = True
    await session.commit()
    if not was_verified:
        await notify_user_sponsors_verified(db_user, session, bot)
        await check_referral_reward(db_user, session, bot)


async def _proceed_after_tos(message: Message, db_user: User, session: AsyncSession, bot: Bot) -> None:
    """Runs once tos_accepted is guaranteed True — the sponsor wall (if any
    providers are configured) is the next gate, then the main menu."""
    was_verified = db_user.sponsors_verified
    if not db_user.sponsors_verified and (settings.tgrass_code or settings.botohub_key):
        if not await run_sponsor_wall_check(message, db_user, session, bot):
            return
    db_user.sponsors_verified = True
    await session.commit()
    if not was_verified:
        # Only on the genuine transition into verified — otherwise an
        # already-verified user (e.g. stuck in the "insufficient sponsors"
        # state forever) would get renotified on every single /start.
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
    # only after "Принимаю" does the sponsor wall (if configured) run. The
    # full gate (text + buttons) is only ever sent once per user — repeat
    # /start presses while still pending get a short reminder instead of
    # another full copy, so mashing /start can't spam duplicates.
    if not db_user.tos_accepted:
        if db_user.tos_gate_shown:
            await message.answer("⬆️ Прими соглашение в сообщении выше, чтобы продолжить.")
            return
        db_user.tos_gate_shown = True
        await session.commit()
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
    has_chats = await ChatRepository(session).has_owned_chats(db_user.user_id)
    kb = main_menu_kb(has_chats)

    if photo:
        try:
            await callback.message.delete()
            await callback.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
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

    if not await run_sponsor_wall_check(callback, db_user, session, bot):
        return

    # All subscribed — ToS is always accepted by this point (the sponsor
    # wall only ever starts after it), so go straight to the main menu.
    await _mark_sponsors_verified_and_notify(db_user, session, bot)

    repo = ContentRepository(session)
    text = await repo.get_text("welcome")
    photo = await repo.get_photo("welcome")
    kb = main_menu_kb(await ChatRepository(session).has_owned_chats(db_user.user_id))

    await callback.answer("✅ Проверка пройдена!")
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


@router.callback_query(lambda c: c.data == "sponsor_skip")
async def cb_sponsor_skip(callback: CallbackQuery, db_user: User) -> None:
    if not settings.tgrass_code and not settings.botohub_key:
        await callback.answer(
            "⚠️ Проверка подписок временно недоступна. Попробуйте позже.",
            show_alert=True,
        )
        return

    count = len(_current_items(db_user))
    if count == 0:
        await callback.answer(
            "✅ Скипать уже нечего — нажми «Я подписался на все каналы».",
            show_alert=True,
        )
        return

    text = (
        f"❓ Вы уверены что хотите скипнуть {count} {sponsors_word(count)}?\n\n"
        f"Если да — оплатите {count}⭐."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Оплатить {count}⭐", callback_data="sponsor_skip_confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="sponsor_check"),
    ]])
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(lambda c: c.data == "sponsor_skip_confirm")
async def cb_sponsor_skip_confirm(callback: CallbackQuery, db_user: User, bot: Bot) -> None:
    count = len(_current_items(db_user))
    if count == 0:
        await callback.answer(
            "✅ Скипать уже нечего — нажми «Я подписался на все каналы».",
            show_alert=True,
        )
        return

    await callback.answer()
    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title="Скип проверки спонсоров",
            description=f"Пропустить проверку подписки на {count} {sponsors_word(count)}",
            payload=f"sponsor_skip:{db_user.user_id}",
            currency="XTR",
            prices=[LabeledPrice(label="Скип спонсоров", amount=count)],
        )
    except Exception:
        logger.warning("Failed to send sponsor-skip invoice to uid=%s", db_user.user_id, exc_info=True)
        try:
            await callback.message.answer("⚠️ Не удалось создать счёт на оплату. Попробуйте позже.")
        except Exception:
            pass


@router.pre_checkout_query()
async def process_sponsor_skip_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    if not pre_checkout_query.invoice_payload.startswith("sponsor_skip:"):
        await pre_checkout_query.answer(ok=False, error_message="Неизвестный платёж.")
        return
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def msg_sponsor_skip_paid(message: Message, db_user: User, session: AsyncSession, bot: Bot) -> None:
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("sponsor_skip:"):
        return
    try:
        payload_user_id = int(payload.split(":", 1)[1])
    except (ValueError, IndexError):
        payload_user_id = None
    if payload_user_id != db_user.user_id:
        logger.warning(
            "Sponsor-skip payment payload user mismatch: payload=%s actual_uid=%s",
            payload, db_user.user_id,
        )
        return

    if db_user.sponsor_wave == 3:
        # Nothing left to skip — e.g. they subscribed for real via "Я
        # подписался" (or an earlier duplicate payment already completed
        # this) before paying a still-open invoice. Refund instead of
        # silently keeping Stars for something already delivered for free.
        try:
            await bot.refund_star_payment(
                db_user.user_id, message.successful_payment.telegram_payment_charge_id,
            )
            await message.answer("↩️ Ты уже прошёл проверку спонсоров — оплата возвращена.")
        except Exception:
            logger.warning(
                "Could not refund stale sponsor-skip payment for uid=%s", db_user.user_id, exc_info=True,
            )
            await message.answer("✅ Оплата получена, но проверка спонсоров уже была пройдена ранее.")
        return

    wave_state = skip_current_wave(db_user)
    await session.commit()

    if wave_state.status == "pending":
        # Only reachable if a second wave was ever frozen (currently never
        # happens — MAX_WAVES caps waves at one — kept correct in case that
        # changes) — the skip only ever covers the wave just paid for.
        await message.answer(
            "✅ Спонсоры пропущены!\n\n" + sponsor_wave_text(wave_state.wave, wave_state.total_waves),
            parse_mode="HTML",
            reply_markup=sponsor_wave_markup(wave_state.items or []),
        )
        return

    await _mark_sponsors_verified_and_notify(db_user, session, bot)
    await message.answer("✅ Спонсоры пропущены, спасибо за оплату!")
    await _send_main_menu(message, db_user, session)


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
