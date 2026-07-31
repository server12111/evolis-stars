import asyncio
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import User
from bot.database.repositories.content import ContentRepository
from bot.database.repositories.settings import SettingsRepository
from bot.database.repositories.user import UserRepository
from bot.keyboards.main import main_menu_kb
from bot.services.adv import send_ad
from bot.services.background import spawn_background
from bot.services.captcha import generate_fruit_captcha
from bot.services.country_notice import ensure_country_notice
from bot.services.phone import (
    matching_country_code,
    normalize_phone,
    phone_rejected_text,
    phone_request_keyboard,
    prompt_phone,
)
from bot.services.referral import (
    check_referral_reward,
    mark_referral_phone_accepted,
    notify_referrer_joined,
    notify_referrer_phone_rejected,
    notify_referrer_sponsors_verified,
    notify_user_sponsors_verified,
    reward_returning_referral,
)
from bot.services.sponsor_results import all_configured_integrations_failed
from bot.services.sponsor_waves import (
    evaluate_waves,
    sponsor_wave_markup,
    sponsor_wave_text,
)
from bot.states.captcha import CaptchaStates

router = Router()
settings = get_settings()


def _captcha_kb(target: str, grid: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for emoji in grid:
        builder.button(text=emoji, callback_data=f"captcha:{emoji}")
    builder.adjust(3)
    return builder.as_markup()


def _captcha_text(target: str, retry: bool = False) -> str:
    prefix = "Неверно! Попробуй ещё раз.\n\n" if retry else ""
    return f"🤖 <b>ПРОВЕРКА НА РОБОТА</b>\n\n{prefix}Нажми на кнопку, где изображено {target}"


async def _send_main_menu(message: Message, user: User, session: AsyncSession) -> None:
    repo = ContentRepository(session)
    text = await repo.get_text("welcome")
    photo = await repo.get_photo("welcome")

    if photo:
        await message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=main_menu_kb())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb())


async def _show_captcha(message: Message, state: FSMContext) -> None:
    if await state.get_state() == CaptchaStates.waiting.state:
        return
    
    target, grid = generate_fruit_captcha()
    await state.set_state(CaptchaStates.waiting)
    await state.update_data(captcha_target=target)
    await message.answer(
        _captcha_text(target),
        parse_mode="HTML",
        reply_markup=_captcha_kb(target, grid),
    )


async def _phone_verification_enabled(session: AsyncSession) -> bool:
    return await SettingsRepository(session).get_bool(
        "phone_verification_enabled",
        True,
    )


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

    # Admins bypass sponsor wall
    if db_user.is_admin or db_user.user_id in settings.admin_id_list:
        db_user.sponsors_verified = True
        await session.commit()
        await _send_main_menu(message, db_user, session)
        return

    # Show sponsor wall if not verified yet
    if not db_user.sponsors_verified and (settings.tgrass_code or settings.botohub_key):
        import asyncio as _asyncio

        from bot.services.botohub import check_botohub
        from bot.services.tgrass import check_tgrass

        tgrass_result, botohub_result = await _asyncio.gather(
            check_tgrass(
                db_user.user_id,
                settings.tgrass_code,
                is_premium=bool(message.from_user and message.from_user.is_premium),
                username=message.from_user.username if message.from_user else None,
                lang=message.from_user.language_code or "ru" if message.from_user else "ru",
            ),
            check_botohub(db_user.user_id, settings.botohub_key),
            return_exceptions=True,
        )
        integration_error = all_configured_integrations_failed(
            tgrass_configured=bool(settings.tgrass_code),
            tgrass_result=tgrass_result,
            botohub_configured=bool(settings.botohub_key),
            botohub_result=botohub_result,
        )
        if integration_error:
            await message.answer(
                "⚠️ Не удалось проверить подписки прямо сейчас. Попробуйте отправить /start ещё раз через несколько секунд."
            )
            return
        wave_size = min(
            6,
            max(1, await SettingsRepository(session).get_int(
                "sponsor_max_channels",
                6,
            )),
        )
        wave_state = evaluate_waves(
            db_user,
            tgrass_result=tgrass_result,
            botohub_result=botohub_result,
            wave_size=wave_size,
        )
        
        if db_user.referrer_id and not db_user.referral_reward_given:
            from bot.services.sponsor_waves import _load
            from datetime import datetime, timezone
            
            total_sponsors = len(_load(db_user.sponsor_wave_one)) + len(_load(db_user.sponsor_wave_two))
            if total_sponsors < 3:
                rejected_referrer = db_user.referrer_id
                db_user.referrer_id = None
                
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                is_recently_created = (now - db_user.created_at).total_seconds() < 86400
                
                if is_recently_created:
                    import html
                    username_display = (
                        f"@{html.escape(message.from_user.username)}"
                        if message.from_user and message.from_user.username
                        else html.escape(message.from_user.first_name or str(message.from_user.id)) if message.from_user else "Пользователь"
                    )
                    try:
                        await bot.send_message(
                            rejected_referrer,
                            f"❌ Пользователю <b>{username_display}</b> выдало меньше 3 спонсоров (от BotoHub и TGrass).\n"
                            f"Награда за него не будет засчитана.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass

        await session.commit()
        if wave_state.status == "unavailable":
            await message.answer(
                "⚠️ Не удалось проверить обязательных спонсоров. "
                "Попробуйте отправить /start ещё раз через несколько секунд."
            )
            return
        if wave_state.status == "pending":
            await message.answer(
                sponsor_wave_text(
                    wave_state.wave,
                    wave_state.total_waves,
                ),
                parse_mode="HTML",
                reply_markup=sponsor_wave_markup(wave_state.items or []),
            )
            return

    if (
        await _phone_verification_enabled(session)
        and not db_user.phone_verified
    ):
        await prompt_phone(message, state)
        return

    if not db_user.sponsors_verified:
        await _show_captcha(message, state)
        return

    await ensure_country_notice(db_user, session, bot)
    await _send_main_menu(message, db_user, session)


@router.callback_query(lambda c: c.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
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
    from bot.services.botohub import check_botohub
    from bot.services.tgrass import check_tgrass

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

    tgrass_result, botohub_result = await asyncio.gather(
        check_tgrass(
            db_user.user_id,
            settings.tgrass_code,
            is_premium=bool(callback.from_user and callback.from_user.is_premium),
            username=callback.from_user.username if callback.from_user else None,
            lang=callback.from_user.language_code or "ru" if callback.from_user else "ru",
        ),
        check_botohub(db_user.user_id, settings.botohub_key),
        return_exceptions=True,
    )

    integration_error = all_configured_integrations_failed(
        tgrass_configured=bool(settings.tgrass_code),
        tgrass_result=tgrass_result,
        botohub_configured=bool(settings.botohub_key),
        botohub_result=botohub_result,
    )
    if integration_error:
        await callback.answer(
            "⚠️ Интеграция временно недоступна. Попробуйте ещё раз позже.",
            show_alert=True,
        )
        return

    previous_wave = db_user.sponsor_wave
    wave_size = min(
        6,
        max(1, await SettingsRepository(session).get_int(
            "sponsor_max_channels",
            6,
        )),
    )
    wave_state = evaluate_waves(
        db_user,
        tgrass_result=tgrass_result,
        botohub_result=botohub_result,
        wave_size=wave_size,
    )
    await session.commit()

    if wave_state.status == "unavailable":
        await callback.answer(
            "⚠️ Не удалось проверить спонсоров. Попробуйте ещё раз позже.",
            show_alert=True,
        )
        return

    if wave_state.status == "pending":
        text = sponsor_wave_text(
            wave_state.wave,
            wave_state.total_waves,
        )
        markup = sponsor_wave_markup(wave_state.items or [])
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=markup,
            )
        except Exception:
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=markup,
            )
        if previous_wave == 1 and wave_state.wave == 2:
            await callback.answer(
                "Готово! Теперь подпишись на оставшиеся каналы."
            )
        else:
            await callback.answer(
                "Вы подписались ещё не на все обязательные каналы.",
                show_alert=True,
            )
        return

    # All subscribed — request the phone before the captcha.
    await callback.answer()
    if (
        await _phone_verification_enabled(session)
        and not db_user.phone_verified
    ):
        await prompt_phone(callback.message, state)
        return
    await _show_captcha(callback.message, state)


@router.message(F.contact)
async def msg_phone_contact(
    message: Message,
    db_user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    if db_user.phone_verified:
        await state.clear()
        await message.answer(
            "✅ Номер уже подтверждён.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    contact = message.contact
    if not contact or contact.user_id != message.from_user.id:
        await message.answer(
            "❌ Отправьте именно свой номер кнопкой ниже.",
            reply_markup=phone_request_keyboard(),
        )
        return

    if (
        (settings.tgrass_code or settings.botohub_key)
        and not db_user.sponsors_verified
        and db_user.sponsor_wave != 3
    ):
        await state.clear()
        await message.answer(
            "❌ Сначала подпишись на всех обязательных спонсоров. "
            "Отправь /start, чтобы продолжить."
        )
        return

    if not await _phone_verification_enabled(session):
        await state.clear()
        await message.answer(
            "✅ Проверка номера сейчас отключена.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await _show_captcha(message, state)
        return

    normalized = normalize_phone(contact.phone_number)
    from bot.database.repositories.settings import SettingsRepository

    codes = await SettingsRepository(session).get("phone_allowed_codes")
    country_code = matching_country_code(normalized, codes)
    db_user.phone_number = normalized or None
    db_user.phone_country_code = country_code
    db_user.phone_verified = country_code is not None
    await session.commit()

    if not country_code:
        await notify_referrer_phone_rejected(db_user, session, bot)
        await message.answer(phone_rejected_text(), reply_markup=phone_request_keyboard())
        return

    await mark_referral_phone_accepted(db_user, session, bot)
    await state.clear()
    await message.answer(
        f"✅ Номер принят (код страны +{country_code}).",
        reply_markup=ReplyKeyboardRemove(),
    )

    await _show_captcha(message, state)


@router.callback_query(CaptchaStates.waiting, lambda c: c.data and c.data.startswith("captcha:"))
async def cb_captcha_pick(
    callback: CallbackQuery,
    db_user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    if (
        (settings.tgrass_code or settings.botohub_key)
        and not db_user.sponsors_verified
        and db_user.sponsor_wave != 3
    ):
        await state.clear()
        await callback.answer(
            "Сначала подпишись на всех обязательных спонсоров.",
            show_alert=True,
        )
        await callback.message.answer(
            "Отправь /start, чтобы продолжить подписку."
        )
        return

    if (
        await _phone_verification_enabled(session)
        and not db_user.phone_verified
    ):
        await callback.answer()
        await prompt_phone(callback.message, state)
        return

    data = await state.get_data()
    target = data.get("captcha_target", "")
    picked = callback.data[8:]  # strip "captcha:"

    if picked != target:
        new_target, new_grid = generate_fruit_captcha()
        await state.update_data(captcha_target=new_target)
        await callback.answer("❌ Неверно!", show_alert=False)
        try:
            await callback.message.edit_text(
                _captcha_text(new_target, retry=True),
                parse_mode="HTML",
                reply_markup=_captcha_kb(new_target, new_grid),
            )
        except Exception:
            pass
        return

    # Correct answer
    await state.clear()
    db_user.sponsors_verified = True
    await session.commit()
    await check_referral_reward(db_user, session, bot)
    if not db_user.referral_reward_given:
        await notify_user_sponsors_verified(db_user, session, bot)
        await notify_referrer_sponsors_verified(db_user, session, bot)
    await ensure_country_notice(db_user, session, bot)

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
