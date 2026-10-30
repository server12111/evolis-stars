from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import OwnSponsor
from bot.database.repositories.own_sponsor import OwnSponsorRepository
from bot.services.telegram_chat import is_subscribed, telegram_chat_id

# Distinct from bonus.py's own "own_sponsor:*" callback prefix -- that one
# gates the daily-bonus claim using the SAME OwnSponsor pool but a
# different completion tail (grant the bonus, not open the bot). Reusing
# one prefix for both would make an old bonus-wall message's buttons
# (Telegram never expires inline keyboards on its own) misfire into this
# flow, or vice versa.
CALLBACK_PREFIX = "wallsponsor"


def _wall_kb(active_sponsors: list[OwnSponsor]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sponsor in active_sponsors:
        # Always a real url= button, for both kinds -- a callback_data
        # button can never itself open a link (answering it only clears
        # Telegram's loading spinner, it doesn't navigate anywhere), so an
        # earlier callback-first design here meant a bot-kind sponsor's
        # link only ever opened on a SECOND tap at the earliest. Telegram
        # never reports a url button tap either way, so bot-kind
        # completion is trusted unconditionally on "Проверить" instead
        # (see recheck_own_sponsors) -- there was never a real click
        # signal to "count" via the callback in the first place.
        builder.row(InlineKeyboardButton(text=f"📱 {sponsor.name}", url=sponsor.url))
    builder.row(InlineKeyboardButton(text="✔️ Проверить", callback_data=f"{CALLBACK_PREFIX}:check"))
    return builder.as_markup()


async def has_pending_own_sponsors(session: AsyncSession, user_id: int) -> bool:
    return bool(await OwnSponsorRepository(session).pending_for_user(user_id))


async def show_own_sponsor_wall(
    inner: Message | CallbackQuery, session: AsyncSession, user_id: int, *, alert: str | None = None,
) -> None:
    """Renders the mandatory own-sponsor step -- shown only once the paid
    tgrass/botohub/traffy/flyerhub wave (and any Stars-paid skip of it) is
    already fully resolved. Deliberately has no skip option: unlike the
    paid provider wave, these are the admin's own free promotion, never
    worth trading away for Stars.

    Owns the single answer() call for a CallbackQuery `inner` -- every
    caller must let this function answer rather than answering itself
    first, or a second answer() on the same callback query gets rejected
    by Telegram. Pass `alert` to surface a specific reason (e.g. "not
    subscribed yet") via that one answer instead of a bare ack."""
    pending = await OwnSponsorRepository(session).pending_for_user(user_id)
    text = (
        "📢 <b>Ещё один шаг перед входом в бота</b>\n\n"
        "Загляни к нашим спонсорам ниже, затем нажми «✔️ Проверить». "
        "Этот шаг обязателен и его нельзя пропустить."
    )
    kb = _wall_kb(pending)
    if isinstance(inner, Message):
        await inner.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    async def _answer() -> None:
        if alert:
            await inner.answer(alert, show_alert=True)
        else:
            await inner.answer()

    if not inner.message:
        await _answer()
        return
    try:
        await inner.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await inner.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await _answer()


async def recheck_own_sponsors(bot: Bot | None, session: AsyncSession, user_id: int) -> bool:
    """Live-verifies pending channel-kind sponsors via bot.get_chat_member
    and marks any now-subscribed ones completed. Bot-kind sponsors are
    trusted unconditionally on this "Проверить" press instead -- Telegram
    never reports a url button tap either way (see _wall_kb's own
    docstring for why that button is a plain link, not a callback), so
    there was never a real per-click signal to gate on; the button being
    a genuine link at least gives the user an actual chance to visit it
    first. Returns True once nothing is pending."""
    repo = OwnSponsorRepository(session)
    for sponsor in await repo.pending_for_user(user_id):
        if sponsor.kind == "bot":
            await repo.mark_completed(sponsor.id, user_id)
            continue
        if bot is None or sponsor.kind != "channel" or not sponsor.target:
            continue
        chat_id = telegram_chat_id(sponsor.target)
        if chat_id is None:
            continue
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            continue
        if is_subscribed(member):
            await repo.mark_completed(sponsor.id, user_id)
    return not await repo.pending_for_user(user_id)
