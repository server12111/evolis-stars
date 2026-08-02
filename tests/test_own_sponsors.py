import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import OwnSponsor, User
from bot.database.repositories.own_sponsor import OwnSponsorRepository
from bot.handlers.bonus import (
    cb_bonus,
    cb_own_sponsor_check,
    cb_own_sponsor_visit,
)
from bot.services.telegram_chat import is_bot_admin_in_chat


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), delete=AsyncMock(), answer_photo=AsyncMock()),
    )


class OwnSponsorRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_mark_completed_increments_and_guards_against_double_counting(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U"))
            session.add(OwnSponsor(id=1, kind="bot", url="https://t.me/x", name="X", target_count=5))
            await session.commit()

            repo = OwnSponsorRepository(session)
            first = await repo.mark_completed(1, 1)
            second = await repo.mark_completed(1, 1)

        async with self.sessions() as session:
            sponsor = await session.get(OwnSponsor, 1)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(sponsor.current_count, 1)

    async def test_mark_completed_auto_disables_at_target(self) -> None:
        async with self.sessions() as session:
            session.add(OwnSponsor(id=1, kind="bot", url="https://t.me/x", name="X", target_count=1, current_count=0))
            for uid in (1, 2):
                session.add(User(user_id=uid, first_name="U"))
            await session.commit()

            repo = OwnSponsorRepository(session)
            await repo.mark_completed(1, 1)

        async with self.sessions() as session:
            sponsor = await session.get(OwnSponsor, 1)
            self.assertFalse(sponsor.is_active)

            # Once inactive, further completions should not be countable via
            # pending_for_user (it's filtered to active sponsors only).
            repo = OwnSponsorRepository(session)
            pending = await repo.pending_for_user(2)
        self.assertEqual(pending, [])

    async def test_pending_for_user_excludes_completed_and_inactive(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U"))
            session.add(OwnSponsor(id=1, kind="channel", target="@a", url="https://t.me/a", name="A", target_count=10))
            session.add(OwnSponsor(id=2, kind="bot", url="https://t.me/b", name="B", target_count=10))
            session.add(OwnSponsor(id=3, kind="bot", url="https://t.me/c", name="C", target_count=10, is_active=False))
            await session.commit()

            repo = OwnSponsorRepository(session)
            await repo.mark_completed(2, 1)
            pending = await repo.pending_for_user(1)

        self.assertEqual([s.id for s in pending], [1])


class IsBotAdminInChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_administrator_status_is_true(self) -> None:
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=999)),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        )
        self.assertTrue(await is_bot_admin_in_chat(bot, "@somechannel"))

    async def test_plain_member_status_is_false(self) -> None:
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=999)),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
        )
        self.assertFalse(await is_bot_admin_in_chat(bot, "@somechannel"))

    async def test_api_failure_is_false(self) -> None:
        bot = SimpleNamespace(
            get_me=AsyncMock(side_effect=Exception("boom")),
            get_chat_member=AsyncMock(),
        )
        self.assertFalse(await is_bot_admin_in_chat(bot, "@somechannel"))

    async def test_unresolvable_target_is_false(self) -> None:
        bot = SimpleNamespace(get_me=AsyncMock(), get_chat_member=AsyncMock())
        self.assertFalse(await is_bot_admin_in_chat(bot, "https://t.me/joinchat/abc"))


class BonusGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_bonus_blocked_by_pending_own_sponsor(self) -> None:
        cb = callback()
        user = SimpleNamespace(user_id=1, last_bonus_at=None, stars_balance=0.0)
        sponsor = SimpleNamespace(id=1, kind="channel", url="https://t.me/a", name="A", target=None)
        with (
            patch(
                "bot.handlers.bonus.OwnSponsorRepository.pending_for_user",
                AsyncMock(return_value=[sponsor]),
            ),
            patch("bot.handlers.bonus.OwnSponsorRepository.all_active", AsyncMock(return_value=[sponsor])),
            patch("bot.handlers.bonus.OwnSponsorRepository.completed_sponsor_ids", AsyncMock(return_value=set())),
        ):
            await cb_bonus(cb, user, SimpleNamespace())

        self.assertIsNone(user.last_bonus_at)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Прежде чем забрать бонус", rendered)

    async def test_bonus_granted_when_no_pending_sponsors(self) -> None:
        cb = callback()
        user = SimpleNamespace(user_id=1, last_bonus_at=None, stars_balance=0.0)
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch("bot.handlers.bonus.OwnSponsorRepository.pending_for_user", AsyncMock(return_value=[])),
            patch("bot.handlers.bonus.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.bonus.SettingsRepository.get_float", AsyncMock(side_effect=[0.1, 1.0])),
            patch("bot.handlers.bonus.ContentRepository.get_photo", AsyncMock(return_value=None)),
        ):
            await cb_bonus(cb, user, session)

        self.assertIsNotNone(user.last_bonus_at)
        self.assertGreater(float(user.stars_balance), 0.0)

    async def test_visit_marks_bot_sponsor_completed_and_rerenders_when_others_remain(self) -> None:
        cb = callback()
        user = SimpleNamespace(user_id=1)
        sponsor = SimpleNamespace(id=5, kind="bot", url="https://t.me/spbot", name="SpBot", target=None)
        other_pending = SimpleNamespace(id=6, kind="channel", url="https://t.me/other", name="Other", target="@other")
        with (
            patch("bot.handlers.bonus.OwnSponsorRepository.get", AsyncMock(return_value=sponsor)),
            patch("bot.handlers.bonus.OwnSponsorRepository.mark_completed", AsyncMock(return_value=True)) as mark,
            patch("bot.handlers.bonus.OwnSponsorRepository.all_active", AsyncMock(return_value=[sponsor, other_pending])),
            patch("bot.handlers.bonus.OwnSponsorRepository.completed_sponsor_ids", AsyncMock(return_value={5})),
            patch(
                "bot.handlers.bonus.OwnSponsorRepository.pending_for_user",
                AsyncMock(return_value=[other_pending]),
            ),
        ):
            cb.data = "own_sponsor:visit:5"
            await cb_own_sponsor_visit(cb, user, SimpleNamespace())

        mark.assert_awaited_once_with(5, 1)
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
        self.assertIn("https://t.me/spbot", urls)

    async def test_visit_grants_bonus_immediately_when_it_was_the_last_pending(self) -> None:
        cb = callback()
        user = SimpleNamespace(user_id=1, last_bonus_at=None, stars_balance=0.0)
        sponsor = SimpleNamespace(id=5, kind="bot", url="https://t.me/spbot", name="SpBot", target=None)
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch("bot.handlers.bonus.OwnSponsorRepository.get", AsyncMock(return_value=sponsor)),
            patch("bot.handlers.bonus.OwnSponsorRepository.mark_completed", AsyncMock(return_value=True)),
            patch("bot.handlers.bonus.OwnSponsorRepository.pending_for_user", AsyncMock(return_value=[])),
            patch("bot.handlers.bonus.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.bonus.SettingsRepository.get_float", AsyncMock(side_effect=[0.1, 1.0])),
            patch("bot.handlers.bonus.ContentRepository.get_photo", AsyncMock(return_value=None)),
        ):
            cb.data = "own_sponsor:visit:5"
            await cb_own_sponsor_visit(cb, user, session)

        self.assertIsNotNone(user.last_bonus_at)
        self.assertGreater(float(user.stars_balance), 0.0)
        # Exactly one answer() call across the whole flow (no double-answer
        # on the same callback query).
        cb.answer.assert_awaited_once()

    async def test_check_confirms_channel_subscription_and_grants_bonus_when_last(self) -> None:
        cb = callback()
        user = SimpleNamespace(user_id=1, last_bonus_at=None, stars_balance=0.0)
        session = SimpleNamespace(commit=AsyncMock())
        sponsor = SimpleNamespace(id=1, kind="channel", url="https://t.me/a", name="A", target="@a")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))

        with (
            patch(
                "bot.handlers.bonus.OwnSponsorRepository.pending_for_user",
                AsyncMock(side_effect=[[sponsor], []]),
            ),
            patch("bot.handlers.bonus.OwnSponsorRepository.mark_completed", AsyncMock(return_value=True)) as mark,
            patch("bot.handlers.bonus.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.bonus.SettingsRepository.get_float", AsyncMock(side_effect=[0.1, 1.0])),
            patch("bot.handlers.bonus.ContentRepository.get_photo", AsyncMock(return_value=None)),
        ):
            await cb_own_sponsor_check(cb, user, session, bot)

        mark.assert_awaited_once_with(1, 1)
        self.assertIsNotNone(user.last_bonus_at)
        # Exactly one answer() call across the whole flow (no double-answer
        # on the same callback query).
        cb.answer.assert_awaited_once()

    async def test_check_does_not_grant_bonus_while_still_pending(self) -> None:
        cb = callback()
        user = SimpleNamespace(user_id=1, last_bonus_at=None, stars_balance=0.0)
        sponsor = SimpleNamespace(id=1, kind="channel", url="https://t.me/a", name="A", target="@a")
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))

        with (
            patch(
                "bot.handlers.bonus.OwnSponsorRepository.pending_for_user",
                AsyncMock(return_value=[sponsor]),
            ),
            patch("bot.handlers.bonus.OwnSponsorRepository.mark_completed", AsyncMock()) as mark,
            patch("bot.handlers.bonus.OwnSponsorRepository.all_active", AsyncMock(return_value=[sponsor])),
            patch("bot.handlers.bonus.OwnSponsorRepository.completed_sponsor_ids", AsyncMock(return_value=set())),
        ):
            await cb_own_sponsor_check(cb, user, SimpleNamespace(), bot)

        mark.assert_not_awaited()
        self.assertIsNone(user.last_bonus_at)


if __name__ == "__main__":
    unittest.main()
