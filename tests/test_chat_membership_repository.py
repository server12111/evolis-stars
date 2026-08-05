import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, ChatMembership
from bot.database.repositories.chat_membership import ChatMembershipRepository


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _setup_chat(self, chat_id: int) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=chat_id, title="T", status="active"))
            await session.commit()


class TouchMessageConcurrencyTests(ChatModelsTestCase):
    async def test_insert_conflict_falls_back_to_atomic_update_and_still_counts(self) -> None:
        """Reproduces the race deterministically: this session's own
        "does it exist" UPDATE reports 0 rows (as it genuinely would if a
        concurrent first-message from the same brand-new member — no
        per-user lock protects group chats — hadn't committed its insert
        yet), but by the time this session tries to INSERT, that concurrent
        insert has already landed, so the INSERT violates the unique
        constraint. This must not raise an uncaught IntegrityError (which
        would abort the whole update before it ever reaches the intended
        handler) — it must fall back to the atomic increment instead, so
        the message still gets counted."""
        await self._setup_chat(-3)
        async with self.sessions() as session:
            session.add(ChatMembership(chat_id=-3, user_id=300, message_count=5, joined_at=datetime.utcnow()))
            await session.commit()

        async with self.sessions() as session:
            real_execute = session.execute
            call_count = {"n": 0}

            async def fake_execute(*args, **kwargs):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return SimpleNamespace(rowcount=0)
                return await real_execute(*args, **kwargs)

            with patch.object(session, "execute", side_effect=fake_execute):
                await ChatMembershipRepository(session).touch_message(-3, 300)

        async with self.sessions() as session:
            membership = await ChatMembershipRepository(session).get(-3, 300)
        self.assertEqual(membership.message_count, 6)

    async def test_existing_member_message_count_increments_normally(self) -> None:
        await self._setup_chat(-2)
        async with self.sessions() as session:
            repo = ChatMembershipRepository(session)
            await repo.touch_message(-2, 200)
            await repo.touch_message(-2, 200)
            await repo.touch_message(-2, 200)

        async with self.sessions() as session:
            membership = await ChatMembershipRepository(session).get(-2, 200)
        self.assertEqual(membership.message_count, 3)


if __name__ == "__main__":
    unittest.main()
