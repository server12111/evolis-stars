import unittest
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update, User

from bot.handlers import group_router, router


class _NoNetworkSession(BaseSession):
    """A Bot session that never touches the network — routing/filter
    dispatch never needs to call the Bot API, so any call here is a bug."""

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: float | None = None):
        raise AssertionError(f"Unexpected live API call during routing test: {method}")

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("Unexpected live API call during routing test")


def _make_message(chat_type: str, text: str, update_id: int) -> Update:
    chat = Chat(id=-100123 if chat_type != "private" else 555, type=chat_type)
    user = User(id=555, is_bot=False, first_name="Test")
    message = Message(
        message_id=1,
        date=datetime.utcnow(),
        chat=chat,
        from_user=user,
        text=text,
    )
    return Update(update_id=update_id, message=message)


class GroupChatRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Phase-0 regression guard: private-DM handlers must never fire for a
    group update, and group handlers must never fire for a private one —
    exactly the split bot/handlers/__init__.py + bot/handlers/group/__init__.py
    are supposed to enforce.

    router/group_router are process-wide singletons that can only ever be
    attached to one parent Dispatcher (aiogram raises if you try twice), so
    the dispatcher + probe routers are built once for the whole class rather
    than per test."""

    private_hits: list[str] = []
    group_hits: list[str] = []

    @classmethod
    def setUpClass(cls) -> None:
        probe_private = Router(name="probe_private")

        @probe_private.message(F.text == "ping")
        async def _private_probe(message: Message) -> None:
            cls.private_hits.append(message.text or "")

        probe_group = Router(name="probe_group")

        @probe_group.message(F.text == "ping")
        async def _group_probe(message: Message) -> None:
            cls.group_hits.append(message.text or "")

        router.include_router(probe_private)
        group_router.include_router(probe_group)

        cls.dp = Dispatcher()
        cls.dp.include_router(router)
        cls.dp.include_router(group_router)

    async def asyncSetUp(self) -> None:
        self.bot = Bot(token="123456:test-token", session=_NoNetworkSession())
        self.private_hits.clear()
        self.group_hits.clear()

    async def asyncTearDown(self) -> None:
        await self.bot.session.close()

    async def test_private_message_only_reaches_private_router(self) -> None:
        update = _make_message("private", "ping", update_id=1)
        await self.dp.feed_update(self.bot, update)

        self.assertEqual(self.private_hits, ["ping"])
        self.assertEqual(self.group_hits, [])

    async def test_group_message_only_reaches_group_router(self) -> None:
        update = _make_message("supergroup", "ping", update_id=2)
        await self.dp.feed_update(self.bot, update)

        self.assertEqual(self.private_hits, [])
        self.assertEqual(self.group_hits, ["ping"])

    async def test_plain_group_type_also_reaches_group_router(self) -> None:
        update = _make_message("group", "ping", update_id=3)
        await self.dp.feed_update(self.bot, update)

        self.assertEqual(self.private_hits, [])
        self.assertEqual(self.group_hits, ["ping"])


if __name__ == "__main__":
    unittest.main()
