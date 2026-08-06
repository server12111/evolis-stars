import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BlockedSponsorUrl, User
from bot.database.repositories.blocked_sponsor import BlockedSponsorRepository
from bot.handlers.admin.blocked_sponsors import (
    cb_blocked_sponsor_del,
    cb_blocked_sponsor_del_confirm,
    cb_blocked_sponsor_new,
    cb_blocked_sponsor_scope,
    cb_blocked_sponsors,
    msg_blocked_sponsor_url,
)
from bot.states.admin import AdminBlockedSponsorStates


def admin_user() -> SimpleNamespace:
    return SimpleNamespace(user_id=1, is_admin=True)


def message() -> SimpleNamespace:
    return SimpleNamespace(text="", answer=AsyncMock())


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        data="",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
    )


class FakeState:
    def __init__(self) -> None:
        self._state = None
        self._data: dict = {}

    async def set_state(self, state) -> None:
        self._state = state

    async def clear(self) -> None:
        self._state = None
        self._data = {}

    async def get_data(self) -> dict:
        return dict(self._data)

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class BlockedSponsorRepositoryTests(DbTestCase):
    async def test_create_normalizes_and_dedupes_by_url_key(self) -> None:
        async with self.sessions() as session:
            repo = BlockedSponsorRepository(session)
            first = await repo.create("https://t.me/Sponsor1/")
            second = await repo.create("https://T.ME/sponsor1")

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.url_key, "https://t.me/sponsor1")

        async with self.sessions() as session:
            all_entries = await BlockedSponsorRepository(session).all()
        self.assertEqual(len(all_entries), 1)

    async def test_url_key_set_reflects_normalized_values(self) -> None:
        async with self.sessions() as session:
            repo = BlockedSponsorRepository(session)
            await repo.create("https://t.me/foo")
            await repo.create("https://t.me/bar/")

        async with self.sessions() as session:
            keys = await BlockedSponsorRepository(session).url_key_set()
        self.assertEqual(keys, {"https://t.me/foo", "https://t.me/bar"})

    async def test_delete_removes_entry(self) -> None:
        async with self.sessions() as session:
            entry = await BlockedSponsorRepository(session).create("https://t.me/gone")

        async with self.sessions() as session:
            deleted = await BlockedSponsorRepository(session).delete(entry.id)
        self.assertTrue(deleted)

        async with self.sessions() as session:
            keys = await BlockedSponsorRepository(session).url_key_set()
        self.assertEqual(keys, set())

    async def test_delete_missing_entry_returns_false(self) -> None:
        async with self.sessions() as session:
            deleted = await BlockedSponsorRepository(session).delete(999)
        self.assertFalse(deleted)

    async def test_domain_create_stores_bare_host_and_dedupes(self) -> None:
        async with self.sessions() as session:
            repo = BlockedSponsorRepository(session)
            first = await repo.create(
                "https://api.flyerpartners.com/search?sign=abc123", match_type="domain",
            )
            second = await repo.create(
                "https://API.flyerpartners.com/search?sign=different-token-entirely",
                match_type="domain",
            )

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.url_key, "api.flyerpartners.com")

        async with self.sessions() as session:
            domains = await BlockedSponsorRepository(session).domain_key_set()
        self.assertEqual(domains, {"api.flyerpartners.com"})

    async def test_url_and_domain_entries_for_the_same_host_coexist(self) -> None:
        async with self.sessions() as session:
            repo = BlockedSponsorRepository(session)
            await repo.create("https://example.com/exact-page", match_type="url")
            await repo.create("https://example.com/exact-page", match_type="domain")

        async with self.sessions() as session:
            all_entries = await BlockedSponsorRepository(session).all()
        self.assertEqual(len(all_entries), 2)


class BlockedSponsorAdminFlowTests(DbTestCase):
    async def test_add_flow_url_scope_creates_exact_entry(self) -> None:
        cb = callback()
        state = FakeState()
        async with self.sessions() as session:
            await cb_blocked_sponsor_new(cb, admin_user(), state)
        self.assertEqual(state._state, AdminBlockedSponsorStates.enter_url)

        msg = message()
        msg.text = "https://t.me/broken_sponsor"
        await msg_blocked_sponsor_url(msg, state, admin_user())
        self.assertEqual(state._state, AdminBlockedSponsorStates.choose_scope)

        cb2 = callback()
        cb2.data = "admin:blocked_sponsor_scope:url"
        async with self.sessions() as session:
            await cb_blocked_sponsor_scope(cb2, admin_user(), session, state)

        self.assertIsNone(state._state)
        async with self.sessions() as session:
            keys = await BlockedSponsorRepository(session).url_key_set()
        self.assertIn("https://t.me/broken_sponsor", keys)

    async def test_add_flow_domain_scope_creates_host_entry(self) -> None:
        """The reported case: FlyerHub hands out a freshly-signed link
        every time (?sign=...) -- blocking the domain instead must
        actually cover a later, differently-signed instance of the same
        link, which an exact-url block never would."""
        state = FakeState()
        msg = message()
        msg.text = "https://api.flyerpartners.com/search?sign=lh68hwhjfYnuAmKQeRvHZbt6yrA"
        await msg_blocked_sponsor_url(msg, state, admin_user())

        cb = callback()
        cb.data = "admin:blocked_sponsor_scope:domain"
        async with self.sessions() as session:
            await cb_blocked_sponsor_scope(cb, admin_user(), session, state)

        async with self.sessions() as session:
            repo = BlockedSponsorRepository(session)
            domains = await repo.domain_key_set()
            urls = await repo.url_key_set()
        self.assertEqual(domains, {"api.flyerpartners.com"})
        self.assertEqual(urls, set())

        # A later instance of the "same" sponsor with a different sign
        # token must also be caught -- that's the whole point.
        from bot.services.sponsor_waves import is_sponsor_blocked
        self.assertTrue(is_sponsor_blocked(
            "https://api.flyerpartners.com/search?sign=totally-different-token",
            frozenset(urls), frozenset(domains),
        ))

    async def test_empty_url_is_rejected_without_creating_anything(self) -> None:
        msg = message()
        msg.text = "   "
        state = FakeState()
        await msg_blocked_sponsor_url(msg, state, admin_user())

        async with self.sessions() as session:
            entries = await BlockedSponsorRepository(session).all()
        self.assertEqual(entries, [])
        msg.answer.assert_awaited_once()

    async def test_list_shows_current_entries(self) -> None:
        async with self.sessions() as session:
            await BlockedSponsorRepository(session).create("https://t.me/onelisted")

        cb = callback()
        state = FakeState()
        async with self.sessions() as session:
            await cb_blocked_sponsors(cb, admin_user(), session, state)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Всего: <b>1</b>", rendered)

    async def test_delete_confirm_removes_entry(self) -> None:
        async with self.sessions() as session:
            entry = await BlockedSponsorRepository(session).create("https://t.me/toremove")

        cb = callback()
        cb.data = f"admin:blocked_sponsor_del_confirm:{entry.id}"
        async with self.sessions() as session:
            await cb_blocked_sponsor_del_confirm(cb, admin_user(), session)

        async with self.sessions() as session:
            keys = await BlockedSponsorRepository(session).url_key_set()
        self.assertEqual(keys, set())

    async def test_delete_ask_for_missing_entry_shows_not_found(self) -> None:
        cb = callback()
        cb.data = "admin:blocked_sponsor_del:999"
        async with self.sessions() as session:
            await cb_blocked_sponsor_del(cb, admin_user(), session)

        cb.answer.assert_awaited_once()
        self.assertIn("Не найдено", cb.answer.await_args.args[0])

    async def test_non_admin_is_ignored(self) -> None:
        non_admin = SimpleNamespace(user_id=2, is_admin=False)
        cb = callback()
        state = FakeState()
        async with self.sessions() as session:
            await cb_blocked_sponsors(cb, non_admin, session, state)
        cb.answer.assert_not_awaited()
        cb.message.edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
