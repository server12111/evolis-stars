import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import ChatWallIntegrationCompletion, User
from bot.database.repositories.chat_wall_integration import ChatWallIntegrationRepository
from bot.middlewares.sponsor_wall import settings as sponsor_wall_settings
from bot.services.chat_wall_integrations import (
    WALL_INTEGRATION_FIELDS,
    evaluate_and_credit_integration_wave,
    integration_item_key,
    pending_integration_items,
)


def offers(prefix: str, count: int) -> list[dict]:
    return [{"name": f"Channel {i}", "url": f"https://t.me/{prefix}{i}"} for i in range(count)]


def inner(user_id: int = 20) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, is_premium=False, username=None, language_code="ru"),
    )


class ItemKeyTests(unittest.TestCase):
    def test_url_based_for_stateless_providers(self) -> None:
        self.assertEqual(
            integration_item_key({"provider": "tgrass", "url": "https://t.me/Foo/"}),
            "https://t.me/foo",
        )

    def test_ref_based_for_stateful_providers(self) -> None:
        self.assertEqual(
            integration_item_key({"provider": "traffy", "url": "https://t.me/x", "ref": "assign-1"}),
            "assign-1",
        )


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        for attr in ("tgrass_code", "botohub_key", "traffy_key", "flyerhub_op_key"):
            patcher = patch.object(sponsor_wall_settings, attr, "")
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _make_user(self, user_id: int, **overrides) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=user_id, first_name="U", **overrides))
            await session.commit()


class PendingIntegrationItemsTests(DbTestCase):
    async def test_empty_wave_returns_nothing(self) -> None:
        await self._make_user(1)
        async with self.sessions() as session:
            user = await session.get(User, 1)
            pending = await pending_integration_items(user, session)
        self.assertEqual(pending, [])

    async def test_completed_items_are_excluded(self) -> None:
        await self._make_user(1, wall_integration_wave=1, wall_integration_wave_one='[{"provider":"tgrass","url":"https://t.me/a"},{"provider":"tgrass","url":"https://t.me/b"}]')
        async with self.sessions() as session:
            await ChatWallIntegrationRepository(session).mark_completed(1, "tgrass", "https://t.me/a")
            user = await session.get(User, 1)
            pending = await pending_integration_items(user, session)
        self.assertEqual([item["url"] for item in pending], ["https://t.me/b"])

    async def test_blocklisted_items_are_excluded_without_a_provider_call(self) -> None:
        """Regression: the DB-only hot path must also drop an item an
        admin blocklisted after it was frozen into the wave -- otherwise a
        user stuck on a now-blocked item would never get unstuck on this
        path (only the live "Проверить" path applied the blocklist)."""
        await self._make_user(1, wall_integration_wave=1, wall_integration_wave_one='[{"provider":"tgrass","url":"https://t.me/a"},{"provider":"tgrass","url":"https://t.me/b"}]')
        async with self.sessions() as session:
            from bot.database.repositories.blocked_sponsor import BlockedSponsorRepository
            await BlockedSponsorRepository(session).create("https://t.me/a")
            user = await session.get(User, 1)
            pending = await pending_integration_items(user, session)
        self.assertEqual([item["url"] for item in pending], ["https://t.me/b"])


class EvaluateAndCreditTests(DbTestCase):
    async def test_freezing_a_fresh_wave_credits_nothing(self) -> None:
        """Nothing was shown before this call, so nothing can have
        "resolved" -- a first-ever freeze must never pay out."""
        await self._make_user(1)
        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 3))):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                wave_state, credited = await evaluate_and_credit_integration_wave(inner(1), user, session, None)
        self.assertEqual(wave_state.status, "pending")
        self.assertEqual(credited, 0)
        self.assertEqual(user.wall_integration_wave, 1)

    async def test_resolved_item_credits_exactly_once(self) -> None:
        await self._make_user(1, stars_balance=0)
        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 2))):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        # tg0 no longer reported (user subscribed); tg1 still pending.
        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[offers("tg", 2)[1]])):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                wave_state, credited = await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        self.assertEqual(credited, 1)
        self.assertEqual(wave_state.status, "pending")
        async with self.sessions() as session:
            saved = await session.get(User, 1)
            completed = await ChatWallIntegrationRepository(session).is_completed(1, "tgrass", "https://t.me/tg0")
        self.assertEqual(float(saved.stars_balance), 1.0)
        self.assertTrue(completed)

        # A third recheck reporting tg0 still absent must NOT re-credit --
        # it's already in the completion ledger.
        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[offers("tg", 2)[1]])):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                _, credited_again = await evaluate_and_credit_integration_wave(inner(1), user, session, None)
        self.assertEqual(credited_again, 0)
        async with self.sessions() as session:
            saved = await session.get(User, 1)
        self.assertEqual(float(saved.stars_balance), 1.0)

    async def test_unavailable_provider_credits_nothing(self) -> None:
        await self._make_user(1, stars_balance=0)
        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 2))):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=None)):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                wave_state, credited = await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        self.assertEqual(wave_state.status, "unavailable")
        self.assertEqual(credited, 0)
        async with self.sessions() as session:
            saved = await session.get(User, 1)
        self.assertEqual(float(saved.stars_balance), 0.0)

    async def test_all_offers_resolved_completes_the_wave(self) -> None:
        await self._make_user(1, stars_balance=0)
        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 1))):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                wave_state, credited = await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        self.assertEqual(wave_state.status, "complete")
        self.assertEqual(credited, 1)
        async with self.sessions() as session:
            saved = await session.get(User, 1)
        self.assertEqual(float(saved.stars_balance), 1.0)
        self.assertEqual(getattr(saved, WALL_INTEGRATION_FIELDS.wave), 3)

    async def test_already_complete_wave_never_spends_a_provider_call(self) -> None:
        """Regression: a stale/repeat 'Проверить' press (e.g. in a
        different walled chat) must not re-run initialize_waves against
        fresh provider results and re-freeze a brand-new pending wave for
        an already-fully-resolved user."""
        await self._make_user(1, wall_integration_wave=3)

        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("check_tgrass must not be called once wave==3")

        with patch("bot.services.tgrass.check_tgrass", _must_not_be_called):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                wave_state, credited = await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        self.assertEqual(wave_state.status, "complete")
        self.assertEqual(credited, 0)

    async def test_blocked_sponsor_auto_drop_is_never_paid_for(self) -> None:
        """A sponsor an admin blocklists after being frozen into the wave
        is auto-dropped by evaluate_waves (same as the /start wall) -- but
        that must never be misread as "the user subscribed" and paid."""
        await self._make_user(1, stars_balance=0)
        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 2))):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        async with self.sessions() as session:
            from bot.database.repositories.blocked_sponsor import BlockedSponsorRepository
            await BlockedSponsorRepository(session).create("https://t.me/tg0")

        # Provider still reports tg1 as pending; tg0 is now blocklisted so
        # evaluate_waves drops it from the saved set entirely (not because
        # the user subscribed).
        with patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[offers("tg", 2)[1]])):
            async with self.sessions() as session:
                user = await session.get(User, 1)
                wave_state, credited = await evaluate_and_credit_integration_wave(inner(1), user, session, None)

        self.assertEqual(credited, 0)
        async with self.sessions() as session:
            saved = await session.get(User, 1)
            completed = await ChatWallIntegrationRepository(session).is_completed(1, "tgrass", "https://t.me/tg0")
        self.assertEqual(float(saved.stars_balance), 0.0)
        self.assertFalse(completed)


class RepositoryRaceSafetyTests(DbTestCase):
    async def test_mark_completed_is_race_safe(self) -> None:
        await self._make_user(1)
        async with self.sessions() as session:
            repo = ChatWallIntegrationRepository(session)
            first = await repo.mark_completed(1, "tgrass", "https://t.me/a")
            second = await repo.mark_completed(1, "tgrass", "https://t.me/a")
        self.assertTrue(first)
        self.assertFalse(second)

    async def test_completed_pairs_scoped_per_user(self) -> None:
        await self._make_user(1)
        await self._make_user(2)
        async with self.sessions() as session:
            repo = ChatWallIntegrationRepository(session)
            await repo.mark_completed(1, "tgrass", "https://t.me/a")
            await repo.mark_completed(2, "tgrass", "https://t.me/b")
            pairs_1 = await repo.completed_pairs(1)
        self.assertEqual(pairs_1, {("tgrass", "https://t.me/a")})


if __name__ == "__main__":
    unittest.main()
