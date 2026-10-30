import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat as TgChat
from aiogram.types import Message
from aiogram.types import Update
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.start import cb_wallsponsor_check
from bot.middlewares.sponsor_wall import (
    _BYPASS_PREFIXES,
    SponsorWallMiddleware,
    run_sponsor_wall_check,
    settings as sponsor_wall_settings,
)
from bot.services.own_sponsor_wall import CALLBACK_PREFIX, show_own_sponsor_wall


def inner(user_id: int = 1) -> SimpleNamespace:
    # Not a real aiogram Message/CallbackQuery instance, so
    # show_own_sponsor_wall's isinstance(inner, Message) check is False and
    # it takes the CallbackQuery-shaped branch -- give it that shape.
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, is_premium=False, username=None, language_code="ru"),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )


def callback(data: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
    )


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # No ad-network providers configured -- isolates these tests to the
        # own-sponsor gate itself, matching test_chat_wall_integrations.py's
        # own convention for the same reason.
        for attr in ("tgrass_code", "botohub_key", "traffy_key", "flyerhub_op_key"):
            patcher = patch.object(sponsor_wall_settings, attr, "")
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _make_user(self, user_id: int = 1, **overrides) -> User:
        async with self.sessions() as session:
            user = User(user_id=user_id, first_name="U", sponsor_wave=3, **overrides)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user


class BypassPrefixTests(unittest.TestCase):
    def test_wallsponsor_prefix_is_bypassed(self) -> None:
        """Without this, SponsorWallMiddleware would re-run the full
        4-provider check on every own-sponsor-wall button press, purely to
        reach a button that has nothing to do with the provider wave."""
        self.assertIn("wallsponsor", _BYPASS_PREFIXES)


class RunSponsorWallCheckGateTests(DbTestCase):
    async def test_blocks_on_pending_own_sponsors_once_provider_wave_is_done(self) -> None:
        user = await self._make_user()
        pending_sponsor = SimpleNamespace(id=1, kind="channel", url="https://t.me/a", name="A")
        async with self.sessions() as session:
            with (
                patch(
                    "bot.services.own_sponsor_wall.OwnSponsorRepository.pending_for_user",
                    AsyncMock(return_value=[pending_sponsor]),
                ),
            ):
                result = await run_sponsor_wall_check(inner(), user, session)

        self.assertFalse(result)

    async def test_proceeds_when_no_own_sponsors_pending(self) -> None:
        user = await self._make_user()
        async with self.sessions() as session:
            with (
                patch(
                    "bot.services.own_sponsor_wall.OwnSponsorRepository.pending_for_user",
                    AsyncMock(return_value=[]),
                ),
            ):
                result = await run_sponsor_wall_check(inner(), user, session)

        self.assertTrue(result)

    async def test_already_verified_user_short_circuits_without_checking_own_sponsors(self) -> None:
        """sponsors_verified=True must stay a cheap no-provider-call,
        no-own-sponsor-call short circuit -- same contract as before this
        gate was added."""
        user = await self._make_user(sponsors_verified=True)
        async with self.sessions() as session:
            with patch(
                "bot.services.own_sponsor_wall.OwnSponsorRepository.pending_for_user",
                AsyncMock(side_effect=AssertionError("must not be called")),
            ):
                result = await run_sponsor_wall_check(inner(), user, session)

        self.assertTrue(result)


class BotSponsorButtonTests(DbTestCase):
    """Regression: a bot-kind own sponsor got 22 "completions" with zero
    real clicks through to it. Root cause -- its button was callback_data
    (needed to "count the click"), but a callback button can never itself
    open a link; Telegram never reports a url-button tap either way, so
    there was never a real per-click signal to count in the first place.
    The button is now always a genuine url= button, and completion is
    trusted unconditionally on the separate "Проверить" press instead."""

    async def test_bot_sponsor_renders_as_a_real_url_button_immediately(self) -> None:
        user = await self._make_user()
        sponsor = SimpleNamespace(id=5, kind="bot", url="https://t.me/spbot", name="SpBot")
        cb = inner()
        async with self.sessions() as session:
            with patch(
                "bot.services.own_sponsor_wall.OwnSponsorRepository.pending_for_user",
                AsyncMock(return_value=[sponsor]),
            ):
                await show_own_sponsor_wall(cb, session, user.user_id)

        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
        callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        self.assertIn("https://t.me/spbot", urls)
        self.assertFalse(any(cd and cd.startswith(f"{CALLBACK_PREFIX}:visit:") for cd in callback_datas))

    async def test_check_trusts_and_completes_pending_bot_sponsors(self) -> None:
        user = await self._make_user()
        sponsor = SimpleNamespace(id=5, kind="bot", url="https://t.me/spbot", name="SpBot")
        cb = callback(data=f"{CALLBACK_PREFIX}:check")
        async with self.sessions() as session:
            with (
                patch(
                    "bot.services.own_sponsor_wall.OwnSponsorRepository.pending_for_user",
                    AsyncMock(side_effect=[[sponsor], []]),
                ),
                patch("bot.services.own_sponsor_wall.OwnSponsorRepository.mark_completed", AsyncMock(return_value=True)) as mark,
                patch("bot.handlers.start.notify_user_sponsors_verified", AsyncMock()),
                patch("bot.handlers.start.check_referral_reward", AsyncMock()),
                patch("bot.handlers.start.resolve_pending_reactivation", AsyncMock()),
                patch("bot.handlers.start.ContentRepository.get_text", AsyncMock(return_value="hi")),
                patch("bot.handlers.start.ContentRepository.get_photo", AsyncMock(return_value=None)),
                patch("bot.handlers.start.ChatRepository.has_owned_chats", AsyncMock(return_value=False)),
            ):
                await cb_wallsponsor_check(cb, user, session, bot=None)

        mark.assert_awaited_once_with(5, user.user_id)
        self.assertTrue(user.sponsors_verified)


class WallsponsorCallbackTests(DbTestCase):
    async def test_check_completes_flow_and_marks_verified_when_nothing_pending(self) -> None:
        user = await self._make_user()
        self.assertFalse(user.sponsors_verified)
        cb = callback(data=f"{CALLBACK_PREFIX}:check")
        bot = SimpleNamespace(get_chat_member=AsyncMock())
        async with self.sessions() as session:
            user = await session.get(User, user.user_id)
            with (
                patch(
                    "bot.services.own_sponsor_wall.OwnSponsorRepository.pending_for_user",
                    AsyncMock(return_value=[]),
                ),
                patch("bot.handlers.start.notify_user_sponsors_verified", AsyncMock()),
                patch("bot.handlers.start.check_referral_reward", AsyncMock()),
                patch("bot.handlers.start.resolve_pending_reactivation", AsyncMock()),
                patch("bot.handlers.start.ContentRepository.get_text", AsyncMock(return_value="hi")),
                patch("bot.handlers.start.ContentRepository.get_photo", AsyncMock(return_value=None)),
                patch("bot.handlers.start.ChatRepository.has_owned_chats", AsyncMock(return_value=False)),
            ):
                await cb_wallsponsor_check(cb, user, session, bot)

        self.assertTrue(user.sponsors_verified)

    async def test_check_stays_blocked_while_channel_subscription_unconfirmed(self) -> None:
        user = await self._make_user()
        sponsor = SimpleNamespace(id=1, kind="channel", url="https://t.me/a", name="A", target="@a")
        cb = callback(data=f"{CALLBACK_PREFIX}:check")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))
        async with self.sessions() as session:
            with patch(
                "bot.services.own_sponsor_wall.OwnSponsorRepository.pending_for_user",
                AsyncMock(return_value=[sponsor]),
            ):
                await cb_wallsponsor_check(cb, user, session, bot)

        self.assertFalse(user.sponsors_verified)
        cb.answer.assert_awaited_once()
        self.assertIn("подписался", cb.answer.await_args.args[0])


def _update(user_id: int, text: str = "hello") -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=5,
            date=datetime.utcnow(),
            chat=TgChat(id=user_id, type="private"),
            from_user=TgUser(id=user_id, is_bot=False, first_name="A"),
            text=text,
        ),
    )


class MiddlewareImmediateRegateTests(DbTestCase):
    """Regression: an admin adding a new own sponsor only actually
    re-gated an already-verified user once sponsor_recheck_scheduler's
    ~10-minute bulk reset reached them. Own-sponsor pending status is now
    checked on every single interaction instead, independent of that
    cycle -- see SponsorWallMiddleware's own sponsors_verified branch."""

    async def test_already_verified_user_is_regated_on_next_message_for_a_new_own_sponsor(self) -> None:
        user = await self._make_user(sponsors_verified=True)
        handler = AsyncMock()
        async with self.sessions() as session:
            data = {
                "db_user": user,
                "session": session,
                "state": SimpleNamespace(),
                "bot": SimpleNamespace(),
            }
            with (
                patch(
                    "bot.services.own_sponsor_wall.has_pending_own_sponsors",
                    AsyncMock(return_value=True),
                ),
                patch(
                    "bot.services.own_sponsor_wall.show_own_sponsor_wall",
                    AsyncMock(),
                ) as show_wall,
            ):
                await SponsorWallMiddleware()(handler, _update(user.user_id), data)

        handler.assert_not_awaited()
        show_wall.assert_awaited_once()
        # sponsors_verified must stay True -- the paid provider wave is
        # genuinely still resolved, only our own sponsors reopened.
        self.assertTrue(user.sponsors_verified)

    async def test_already_verified_user_proceeds_when_no_new_own_sponsors(self) -> None:
        user = await self._make_user(sponsors_verified=True)
        handler = AsyncMock(return_value="handled")
        async with self.sessions() as session:
            data = {
                "db_user": user,
                "session": session,
                "state": SimpleNamespace(),
                "bot": SimpleNamespace(),
            }
            with patch(
                "bot.services.own_sponsor_wall.OwnSponsorRepository.pending_for_user",
                AsyncMock(return_value=[]),
            ):
                result = await SponsorWallMiddleware()(handler, _update(user.user_id), data)

        handler.assert_awaited_once()
        self.assertEqual(result, "handled")


if __name__ == "__main__":
    unittest.main()
