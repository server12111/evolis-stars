import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat as TgChat
from aiogram.types import Message
from aiogram.types import Update
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, ChatSponsorWallSponsor, User
from bot.database.repositories.chat_sponsor_wall import ChatSponsorWallRepository
from bot.handlers.group.chat_bonus import _origin_state, _pending_sponsor_state, try_link_pending_sponsor
from bot.handlers.group.chat_sponsor_wall import cb_wall_check, wall_subscribe_kb
from bot.handlers.group.games_tower import msg_tower_start
from bot.middlewares.chat_sponsor_wall import ChatSponsorWallMiddleware
from bot.middlewares.sponsor_wall import settings as sponsor_wall_settings
from bot.states.group import PendingSponsorAddStates


def _update(chat_id: int, user_id: int, text: str = "hello") -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=5,
            date=datetime.utcnow(),
            chat=TgChat(id=chat_id, type="supergroup"),
            from_user=TgUser(id=user_id, is_bot=False, first_name="A"),
            text=text,
        ),
    )


def _my_chat_member(chat_id: int, chat_type: str, from_user_id: int, status: str, title: str = "Sponsor", username: str | None = None):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=chat_type, title=title, username=username),
        from_user=SimpleNamespace(id=from_user_id),
        new_chat_member=SimpleNamespace(status=status),
        old_chat_member=SimpleNamespace(status="left"),
    )


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        # The chat sponsor wall now also evaluates the ad-network
        # integration wave (bot.services.chat_wall_integrations), which
        # reuses evaluate_provider_wave -- reads these same settings the
        # /start wall does. A real local .env can have real provider keys
        # configured; blank them out here so these tests never make a real
        # network call and reliably resolve to an empty/complete wave.
        for attr in ("tgrass_code", "botohub_key", "traffy_key", "flyerhub_op_key"):
            patcher = patch.object(sponsor_wall_settings, attr, "")
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _make_chat(
        self, chat_id: int, owner_id: int, max_sponsors: int = 3, wall_integration_enabled: bool = False,
    ) -> Chat:
        async with self.sessions() as session:
            chat = Chat(
                chat_id=chat_id, title="C", owner_user_id=owner_id, sponsor_wall_max_sponsors=max_sponsors,
                wall_integration_enabled=wall_integration_enabled,
            )
            session.add(chat)
            await session.commit()
            return chat

    async def _make_user(self, user_id: int, balance: str = "0", **overrides) -> User:
        async with self.sessions() as session:
            user = User(user_id=user_id, first_name="U", stars_balance=Decimal(balance), **overrides)
            session.add(user)
            await session.commit()
            return user

    async def _add_sponsor(self, chat_id: int, sponsor_chat_id: int, **overrides) -> ChatSponsorWallSponsor:
        async with self.sessions() as session:
            defaults = dict(sponsor_type="channel", title="Sponsor", username=f"sp{sponsor_chat_id}")
            defaults.update(overrides)
            sponsor = ChatSponsorWallSponsor(chat_id=chat_id, sponsor_chat_id=sponsor_chat_id, **defaults)
            session.add(sponsor)
            await session.commit()
            await session.refresh(sponsor)
            return sponsor


class RepositoryTests(DbTestCase):
    async def test_add_respects_per_chat_max(self) -> None:
        await self._make_chat(-1, 1, max_sponsors=1)
        async with self.sessions() as session:
            repo = ChatSponsorWallRepository(session)
            first = await repo.add(-1, -100, "channel", "A", "a", max_sponsors=1)
            second = await repo.add(-1, -200, "channel", "B", "b", max_sponsors=1)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    async def test_add_rejects_duplicate_sponsor_chat(self) -> None:
        await self._make_chat(-1, 1, max_sponsors=5)
        async with self.sessions() as session:
            repo = ChatSponsorWallRepository(session)
            first = await repo.add(-1, -100, "channel", "A", "a", max_sponsors=5)
            dup = await repo.add(-1, -100, "channel", "A", "a", max_sponsors=5)
        self.assertIsNotNone(first)
        self.assertIsNone(dup)

    async def test_mark_completed_is_race_safe(self) -> None:
        await self._make_chat(-1, 1)
        await self._make_user(10)
        sponsor = await self._add_sponsor(-1, -100)
        async with self.sessions() as session:
            repo = ChatSponsorWallRepository(session)
            first = await repo.mark_completed(sponsor.id, 10)
            second = await repo.mark_completed(sponsor.id, 10)
        self.assertTrue(first)
        self.assertFalse(second)

    async def test_toggle_and_delete_are_ownership_scoped(self) -> None:
        await self._make_chat(-1, 1)
        await self._make_chat(-2, 2)
        sponsor = await self._add_sponsor(-1, -100)
        async with self.sessions() as session:
            repo = ChatSponsorWallRepository(session)
            wrong_chat = await repo.toggle(sponsor.id, -2)
            right_chat = await repo.toggle(sponsor.id, -1)
        self.assertIsNone(wrong_chat)
        self.assertIsNotNone(right_chat)
        self.assertFalse(right_chat.is_active)

        async with self.sessions() as session:
            repo = ChatSponsorWallRepository(session)
            wrong_delete = await repo.delete(sponsor.id, -2)
            right_delete = await repo.delete(sponsor.id, -1)
        self.assertFalse(wrong_delete)
        self.assertTrue(right_delete)


class GeneralizedPendingSponsorFlowTests(DbTestCase):
    async def _seed_pending_wall(self, storage, bot_id, user_id, sponsor_type, origin_chat_id, wall_chat_id):
        pending = _pending_sponsor_state(storage, bot_id, user_id)
        await pending.set_state(PendingSponsorAddStates.awaiting_add)
        await pending.update_data(
            sponsor_type=sponsor_type, origin_chat_id=origin_chat_id,
            purpose="wall", wall_chat_id=wall_chat_id,
        )

    async def test_wall_purpose_persists_directly_to_wall_table_not_fsm(self) -> None:
        await self._make_chat(-50, 42, max_sponsors=3)
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        await self._seed_pending_wall(storage, 1, 42, "channel", -50, -50)

        event = _my_chat_member(-900, "channel", 42, "administrator", title="Sponsor Ch", username="sponsorch")
        async with self.sessions() as session:
            handled = await try_link_pending_sponsor(bot, storage, event, session)

        self.assertTrue(handled)
        async with self.sessions() as session:
            sponsors = await ChatSponsorWallRepository(session).list_active(-50)
        self.assertEqual(len(sponsors), 1)
        self.assertEqual(sponsors[0].sponsor_chat_id, -900)

        # Must NOT have also polluted the bonus-creation FSM's own state.
        origin = _origin_state(storage, 1, -50, 42)
        origin_data = await origin.get_data()
        self.assertNotIn("sponsors", origin_data)

        pending = _pending_sponsor_state(storage, 1, 42)
        self.assertIsNone(await pending.get_state())
        bot.send_message.assert_awaited_once()

    async def test_wall_purpose_respects_max_sponsors(self) -> None:
        await self._make_chat(-50, 42, max_sponsors=1)
        await self._add_sponsor(-50, -800)
        storage = MemoryStorage()
        bot = SimpleNamespace(id=1, send_message=AsyncMock())
        await self._seed_pending_wall(storage, 1, 42, "channel", -50, -50)

        event = _my_chat_member(-900, "channel", 42, "administrator")
        async with self.sessions() as session:
            handled = await try_link_pending_sponsor(bot, storage, event, session)

        self.assertTrue(handled)
        async with self.sessions() as session:
            sponsors = await ChatSponsorWallRepository(session).list_active(-50)
        self.assertEqual(len(sponsors), 1)  # the pre-existing one, new one rejected
        self.assertEqual(sponsors[0].sponsor_chat_id, -800)


class MiddlewareTests(DbTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Module-level debounce dict (bot/middlewares/chat_sponsor_wall.py)
        # persists across tests in the same process -- several tests here
        # reuse the same (chat_id, user_id) pair, and without clearing this
        # a wall message sent by an earlier test can suppress a later
        # test's expected send_message call within the cooldown window.
        from bot.middlewares.chat_sponsor_wall import _last_wall_shown
        _last_wall_shown.clear()

    async def _run(self, chat: Chat, user_id: int, session, bot=None):
        handler = AsyncMock(return_value="handled")
        bot = bot or SimpleNamespace(
            delete_message=AsyncMock(), send_message=AsyncMock(),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
        )
        update = _update(chat.chat_id, user_id)
        data = {
            "session": session,
            "bot": bot,
            "event_chat": update.message.chat,
            "event_from_user": update.message.from_user,
            "chat": chat,
        }
        result = await ChatSponsorWallMiddleware()(handler, update, data)
        return handler, bot, result

    async def test_no_active_sponsors_is_a_full_noop(self) -> None:
        chat = await self._make_chat(-1, 1)
        await self._make_user(10)
        async with self.sessions() as session:
            handler, bot, result = await self._run(chat, 10, session)
        handler.assert_awaited_once()
        bot.delete_message.assert_not_awaited()
        self.assertEqual(result, "handled")

    async def test_paid_sponsors_alone_activate_the_wall_with_zero_owner_sponsors(self) -> None:
        """Confirmed with the user: the paid-sponsors toggle is an
        independent activation switch -- an owner must be able to run a
        paid-only wall without ever adding one of their own sponsors."""
        chat = await self._make_chat(-1, 1, wall_integration_enabled=True)
        await self._make_user(20)

        # "member" only for the group chat itself (the admin-exemption
        # check) -- NOT for the offered sponsor channel, or
        # _drop_confirmed_subscriptions would read that as "already
        # subscribed" and drop the only offer, defeating the test.
        async def _get_chat_member(chat_id, user_id):
            if chat_id == -1:
                return SimpleNamespace(status="member")
            return SimpleNamespace(status="left")

        bot = SimpleNamespace(
            delete_message=AsyncMock(), send_message=AsyncMock(),
            get_chat_member=AsyncMock(side_effect=_get_chat_member),
        )
        # A genuine ad-network offer to gate on -- with every provider
        # blanked out (this file's own isolation), there'd be nothing to
        # show and the wall would correctly self-resolve to "nothing
        # pending" instead of exercising the activation path this test is
        # actually about.
        with (
            patch.object(sponsor_wall_settings, "tgrass_code", "cfg"),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[{"name": "S", "url": "https://t.me/s1"}])),
        ):
            async with self.sessions() as session:
                handler, bot, result = await self._run(chat, 20, session, bot=bot)
        handler.assert_not_awaited()
        bot.delete_message.assert_awaited_once()
        bot.send_message.assert_awaited_once()

    async def test_owner_is_exempt_without_any_api_call(self) -> None:
        chat = await self._make_chat(-1, 1)
        await self._add_sponsor(-1, -100)
        await self._make_user(1)
        bot = SimpleNamespace(
            delete_message=AsyncMock(), send_message=AsyncMock(),
            get_chat_member=AsyncMock(side_effect=AssertionError("must not be called for the owner")),
        )
        async with self.sessions() as session:
            handler, bot, result = await self._run(chat, 1, session, bot=bot)
        handler.assert_awaited_once()
        bot.delete_message.assert_not_awaited()

    async def test_telegram_chat_admin_is_exempt(self) -> None:
        chat = await self._make_chat(-1, 1)
        await self._add_sponsor(-1, -100)
        await self._make_user(20)
        bot = SimpleNamespace(
            delete_message=AsyncMock(), send_message=AsyncMock(),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        )
        async with self.sessions() as session:
            handler, bot, result = await self._run(chat, 20, session, bot=bot)
        handler.assert_awaited_once()
        bot.delete_message.assert_not_awaited()

    async def test_unsubscribed_member_message_is_deleted_and_handler_never_runs(self) -> None:
        chat = await self._make_chat(-1, 1)
        await self._add_sponsor(-1, -100)
        await self._make_user(20)
        bot = SimpleNamespace(
            delete_message=AsyncMock(), send_message=AsyncMock(),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
        )
        async with self.sessions() as session:
            handler, bot, result = await self._run(chat, 20, session, bot=bot)
        handler.assert_not_awaited()
        bot.delete_message.assert_awaited_once()
        bot.send_message.assert_awaited_once()
        self.assertIsNone(result)

    async def test_fully_completed_member_passes_through_without_api_call(self) -> None:
        # wall_integration_wave=3: already resolved on the ad-network side
        # too (see test_first_check_still_freezes_integration_wave_even_
        # when_owner_side_already_done below for the still-pending case) --
        # this is the true zero-cost "nothing left to check at all" path.
        chat = await self._make_chat(-1, 1)
        sponsor = await self._add_sponsor(-1, -100)
        await self._make_user(20, wall_integration_wave=3)
        async with self.sessions() as session:
            await ChatSponsorWallRepository(session).mark_completed(sponsor.id, 20)

        bot = SimpleNamespace(
            delete_message=AsyncMock(), send_message=AsyncMock(),
            get_chat_member=AsyncMock(side_effect=AssertionError("must not be called once fully completed")),
        )
        async with self.sessions() as session:
            handler, bot, result = await self._run(chat, 20, session, bot=bot)
        handler.assert_awaited_once()
        bot.delete_message.assert_not_awaited()

    async def test_first_check_still_freezes_integration_wave_even_when_owner_side_already_done(self) -> None:
        """A user who already satisfied the owner sponsors but has never
        had their ad-network integration wave initialized (wall_integration_
        wave == 0, the User default) must still go through the one-time
        freeze -- with no providers configured in tests this resolves to
        "complete" immediately and the message passes through, but the
        admin-exemption check IS reachable (unlike the fully-resolved case
        above)."""
        chat = await self._make_chat(-1, 1, wall_integration_enabled=True)
        sponsor = await self._add_sponsor(-1, -100)
        await self._make_user(20)  # wall_integration_wave defaults to 0
        async with self.sessions() as session:
            await ChatSponsorWallRepository(session).mark_completed(sponsor.id, 20)

        async with self.sessions() as session:
            handler, bot, result = await self._run(chat, 20, session)
        handler.assert_awaited_once()
        bot.delete_message.assert_not_awaited()

        async with self.sessions() as session:
            saved = await session.get(User, 20)
        self.assertEqual(saved.wall_integration_wave, 3)  # frozen empty -> resolved (no providers configured)

    async def test_unavailable_provider_still_blocks_even_when_owner_side_done(self) -> None:
        """A provider outage during the first-ever integration freeze must
        not silently let the user through -- same as the /start wall's
        own _show_retry path."""
        chat = await self._make_chat(-1, 1, wall_integration_enabled=True)
        sponsor = await self._add_sponsor(-1, -100)
        await self._make_user(20)
        async with self.sessions() as session:
            await ChatSponsorWallRepository(session).mark_completed(sponsor.id, 20)

        with (
            patch.object(sponsor_wall_settings, "tgrass_code", "cfg"),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=None)),
        ):
            async with self.sessions() as session:
                handler, bot, result = await self._run(chat, 20, session)

        handler.assert_not_awaited()
        bot.delete_message.assert_awaited_once()

    async def test_unexpected_exception_during_integration_eval_still_blocks_not_crashes(self) -> None:
        """Regression: an unexpected exception inside the integration-wave
        evaluation must not propagate out of the middleware (which would
        leave the member's blocked message with no wall response at all)
        -- it must degrade to blocking, same as a provider outage."""
        chat = await self._make_chat(-1, 1, wall_integration_enabled=True)
        sponsor = await self._add_sponsor(-1, -100)
        await self._make_user(20)
        async with self.sessions() as session:
            await ChatSponsorWallRepository(session).mark_completed(sponsor.id, 20)

        with patch(
            "bot.services.chat_wall_integrations.evaluate_and_credit_integration_wave",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            async with self.sessions() as session:
                handler, bot, result = await self._run(chat, 20, session)

        handler.assert_not_awaited()
        bot.delete_message.assert_awaited_once()
        bot.send_message.assert_awaited_once()

    async def test_unexpected_exception_on_the_db_only_hot_path_still_blocks_not_crashes(self) -> None:
        """Regression: this is the MORE common path than the wave==0 one
        above -- a user's wave only equals 0 once, ever, so wave in {1,2}
        (the DB-only pending_integration_items check) is what most
        messages hit. An exception there must degrade the same way."""
        chat = await self._make_chat(-1, 1, wall_integration_enabled=True)
        sponsor = await self._add_sponsor(-1, -100)
        await self._make_user(
            20, wall_integration_wave=1,
            wall_integration_wave_one='[{"provider":"tgrass","url":"https://t.me/a"}]',
        )
        async with self.sessions() as session:
            await ChatSponsorWallRepository(session).mark_completed(sponsor.id, 20)

        with patch(
            "bot.services.chat_wall_integrations.pending_integration_items",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            async with self.sessions() as session:
                handler, bot, result = await self._run(chat, 20, session)

        handler.assert_not_awaited()
        bot.delete_message.assert_awaited_once()
        bot.send_message.assert_awaited_once()

    async def test_integration_disabled_for_chat_skips_it_entirely(self) -> None:
        """The owner's per-chat toggle must fully exempt the integration
        side -- no User lookup, no wave touched, no blocking on it -- while
        the owner-sponsor gate keeps working as normal."""
        chat = await self._make_chat(-1, 1)
        sponsor = await self._add_sponsor(-1, -100)
        await self._make_user(20)
        async with self.sessions() as session:
            saved_chat = await session.get(Chat, -1)
            saved_chat.wall_integration_enabled = False
            await session.commit()

        with patch(
            "bot.services.chat_wall_integrations.evaluate_and_credit_integration_wave",
            AsyncMock(side_effect=AssertionError("must not be called when integration is disabled")),
        ):
            async with self.sessions() as session:
                chat = await session.get(Chat, -1)
                handler, bot, result = await self._run(chat, 20, session)

        # Owner sponsor is still unsatisfied -> still blocked, but purely
        # on the owner side.
        handler.assert_not_awaited()
        bot.delete_message.assert_awaited_once()

        async with self.sessions() as session:
            await ChatSponsorWallRepository(session).mark_completed(sponsor.id, 20)
        with patch(
            "bot.services.chat_wall_integrations.evaluate_and_credit_integration_wave",
            AsyncMock(side_effect=AssertionError("must not be called when integration is disabled")),
        ):
            async with self.sessions() as session:
                handler, bot, result = await self._run(chat, 20, session)
        handler.assert_awaited_once()

    async def test_bot_admin_is_exempt_even_as_a_plain_chat_member(self) -> None:
        from unittest.mock import patch

        from bot.config import get_settings

        chat = await self._make_chat(-1, 1)
        await self._add_sponsor(-1, -100)
        await self._make_user(999)
        bot = SimpleNamespace(
            delete_message=AsyncMock(), send_message=AsyncMock(),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
        )
        with patch.object(get_settings(), "admin_ids", "999"):
            async with self.sessions() as session:
                handler, bot, result = await self._run(chat, 999, session, bot=bot)
        handler.assert_awaited_once()
        bot.delete_message.assert_not_awaited()


class CheckCallbackTests(DbTestCase):
    def _callback(self, chat_id: int, user_id: int, target_user_id: int | None = None) -> SimpleNamespace:
        """target_user_id encodes who the wall message was actually shown
        to (see wall_subscribe_kb's user_id param) -- omitted reproduces a
        legacy/unscoped button, matching target_user_id=user_id reproduces
        the normal case of the intended recipient pressing their own
        button."""
        data = f"chatwall:check:{chat_id}"
        if target_user_id is not None:
            data += f":{target_user_id}"
        return SimpleNamespace(
            data=data,
            # is_premium/username/language_code: evaluate_provider_wave
            # (the integration-wave check, also run by cb_wall_check now)
            # reads these unconditionally off from_user.
            from_user=SimpleNamespace(id=user_id, is_premium=False, username=None, language_code="ru"),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

    async def test_wrong_presser_is_turned_away_without_running_any_check(self) -> None:
        """Regression: the check button is scoped to whoever the wall was
        shown to -- a different chat member pressing the SAME message's
        button must not have it silently run against their own account."""
        chat = await self._make_chat(-1, 1)
        sponsor = await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(20, balance="5")  # the intended recipient
        await self._make_user(21, balance="5")  # a different, unrelated member
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(side_effect=AssertionError("must not check membership for the wrong presser")),
        )

        # Wall was shown to user 20; user 21 presses the same button.
        callback = self._callback(-1, 21, target_user_id=20)
        async with self.sessions() as session:
            await cb_wall_check(callback, session, bot)

        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))
        callback.message.edit_text.assert_not_awaited()
        async with self.sessions() as session:
            completed = await ChatSponsorWallRepository(session).is_completed(sponsor.id, 21)
        self.assertFalse(completed)

    async def test_intended_recipient_can_still_press_their_own_button(self) -> None:
        chat = await self._make_chat(-1, 1)
        sponsor = await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(20, balance="5")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))

        callback = self._callback(-1, 20, target_user_id=20)
        async with self.sessions() as session:
            await cb_wall_check(callback, session, bot)

        async with self.sessions() as session:
            completed = await ChatSponsorWallRepository(session).is_completed(sponsor.id, 20)
        self.assertTrue(completed)

    async def test_malformed_recipient_segment_rejected_not_bypassed(self) -> None:
        """A non-numeric parts[3] (forged/malformed callback_data) must fail
        closed -- rejecting the check -- not be silently treated as if no
        recipient had been encoded at all, which would run the check
        unrestricted for anyone."""
        chat = await self._make_chat(-1, 1)
        sponsor = await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(21, balance="5")
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(side_effect=AssertionError("must not check membership on malformed data")),
        )

        callback = SimpleNamespace(
            data="chatwall:check:-1:notanumber",
            from_user=SimpleNamespace(id=21, is_premium=False, username=None, language_code="ru"),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            await cb_wall_check(callback, session, bot)

        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))
        callback.message.edit_text.assert_not_awaited()
        async with self.sessions() as session:
            completed = await ChatSponsorWallRepository(session).is_completed(sponsor.id, 21)
        self.assertFalse(completed)

    async def test_wall_subscribe_kb_encodes_the_recipient_into_the_check_button(self) -> None:
        kb = wall_subscribe_kb([], -1, user_id=20)
        callback_datas = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        self.assertIn("chatwall:check:-1:20", callback_datas)

    async def test_check_resolves_with_zero_owner_sponsors_when_paid_toggle_is_on(self) -> None:
        """The "Стена больше не активна" early-out must not fire just
        because the owner never added their own sponsor -- the paid
        toggle alone is enough to keep the wall (and this check) active."""
        chat = await self._make_chat(-1, 1, wall_integration_enabled=True)
        await self._make_user(20, balance="5")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))

        callback = self._callback(-1, 20)
        async with self.sessions() as session:
            await cb_wall_check(callback, session, bot)

        callback.answer.assert_awaited_once()
        self.assertNotIn("больше не активна", str(callback.answer.await_args.args))

    async def test_checks_membership_by_numeric_chat_id_not_bare_username(self) -> None:
        """Regression: get_chat_member must be called with sponsor_chat_id
        (the numeric id), not sponsor.username -- passing a bare username
        without a leading '@' is rejected by Telegram's Bot API, which
        would permanently block every genuinely-subscribed member."""
        chat = await self._make_chat(-1, 1)
        sponsor = await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(20, balance="5")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))

        async with self.sessions() as session:
            await cb_wall_check(self._callback(-1, 20), session, bot)

        bot.get_chat_member.assert_awaited_once_with(sponsor.sponsor_chat_id, 20)

    async def test_newly_confirmed_owner_sponsor_marks_completed_but_pays_no_rp(self) -> None:
        """Owner-added sponsors are unfunded (the chat owner's own
        promotion) -- subscribing satisfies the gate (is_completed) but no
        longer credits RP⭐️, unlike before this session's change."""
        chat = await self._make_chat(-1, 1)
        sponsor = await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(20, balance="5")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))

        async with self.sessions() as session:
            await cb_wall_check(self._callback(-1, 20), session, bot)
        async with self.sessions() as session:
            user = await session.get(User, 20)
            completed = await ChatSponsorWallRepository(session).is_completed(sponsor.id, 20)
        self.assertEqual(float(user.stars_balance), 5.0)
        self.assertTrue(completed)

        # Pressing check again must not change the balance either.
        async with self.sessions() as session:
            await cb_wall_check(self._callback(-1, 20), session, bot)
        async with self.sessions() as session:
            user = await session.get(User, 20)
        self.assertEqual(float(user.stars_balance), 5.0)

    async def test_still_unsubscribed_reports_alert_not_credit(self) -> None:
        chat = await self._make_chat(-1, 1)
        await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(20, balance="5")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))

        callback = self._callback(-1, 20)
        async with self.sessions() as session:
            await cb_wall_check(callback, session, bot)
        async with self.sessions() as session:
            user = await session.get(User, 20)
        self.assertEqual(float(user.stars_balance), 5.0)
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))

    async def test_unavailable_integration_provider_does_not_report_success(self) -> None:
        chat = await self._make_chat(-1, 1, wall_integration_enabled=True)
        sponsor = await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(20, balance="5")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))

        callback = self._callback(-1, 20)
        with (
            patch.object(sponsor_wall_settings, "tgrass_code", "cfg"),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=None)),
        ):
            async with self.sessions() as session:
                await cb_wall_check(callback, session, bot)

        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))

    async def test_unexpected_exception_still_answers_the_callback(self) -> None:
        """Regression: this is exactly what 'нажимаешь проверить, а оно не
        проверяется' looks like from the user's side -- an unhandled
        exception mid-handler means callback.answer() never fires and
        Telegram shows nothing at all. Must degrade to a blocking alert
        instead of crashing."""
        chat = await self._make_chat(-1, 1, wall_integration_enabled=True)
        await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(20, balance="5")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))

        callback = self._callback(-1, 20)
        with patch(
            "bot.services.chat_wall_integrations.evaluate_and_credit_integration_wave",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            async with self.sessions() as session:
                await cb_wall_check(callback, session, bot)

        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))

    async def test_integration_disabled_for_chat_never_evaluated(self) -> None:
        chat = await self._make_chat(-1, 1)
        await self._add_sponsor(-1, -100, username="sp100")
        await self._make_user(20, balance="5")
        async with self.sessions() as session:
            saved_chat = await session.get(Chat, -1)
            saved_chat.wall_integration_enabled = False
            await session.commit()

        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
        callback = self._callback(-1, 20)
        with patch(
            "bot.services.chat_wall_integrations.evaluate_and_credit_integration_wave",
            AsyncMock(side_effect=AssertionError("must not be called when integration is disabled")),
        ):
            async with self.sessions() as session:
                await cb_wall_check(callback, session, bot)

        callback.message.edit_text.assert_awaited_once()
        callback.answer.assert_awaited_once()
        self.assertNotIn("show_alert", callback.answer.await_args.kwargs)


class GamesToggleTests(DbTestCase):
    async def test_disabled_chat_blocks_game_start(self) -> None:
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            text="башня 5",
            reply=AsyncMock(),
        )
        chat = Chat(chat_id=-1, title="C", games_enabled=False)
        async with self.sessions() as session:
            await msg_tower_start(message, session, chat=chat)
        message.reply.assert_awaited_once()
        self.assertIn("отключены", message.reply.await_args.args[0])

    async def test_enabled_chat_does_not_block_on_the_toggle(self) -> None:
        """games_enabled=True must fall through to the (mocked-away) normal
        flow rather than being rejected by the new check itself."""
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            chat=SimpleNamespace(id=-1),
            text="башня 5",
            reply=AsyncMock(),
        )
        chat = Chat(chat_id=-1, title="C", games_enabled=True)
        async with self.sessions() as session:
            await msg_tower_start(message, session, chat=chat)
        # Falls through past both gates into place_bet(), which will reply
        # about registration/balance -- either way, NOT the games-disabled
        # message specifically.
        message.reply.assert_awaited_once()
        self.assertNotIn("отключены", message.reply.await_args.args[0])


class RouterWiringTests(unittest.TestCase):
    """Regression: chat_sponsor_wall.router (which owns cb_wall_check, the
    "Проверить" button handler) was imported into bot/handlers/group but
    never actually passed to router.include_router(...) -- the callback
    filter was correctly written and unit-tested in isolation the whole
    time, but no real Telegram update could ever reach it, so pressing
    "Проверить" silently did nothing. A plain call-the-function-directly
    unit test can never catch this class of bug (it bypasses routing
    entirely), so this checks the actual router wiring instead."""

    def test_chat_sponsor_wall_router_is_included_in_the_group_router(self) -> None:
        from bot.handlers.group import chat_sponsor_wall
        from bot.handlers.group import router as group_router

        self.assertIn(chat_sponsor_wall.router, group_router.sub_routers)


if __name__ == "__main__":
    unittest.main()
