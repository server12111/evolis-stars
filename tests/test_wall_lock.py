import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User
from bot.middlewares.sponsor_wall import evaluate_provider_wave, settings as sponsor_wall_settings


def inner(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, is_premium=False, username=None, language_code="ru"),
    )


class WallLockConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Regression: a user who doesn't see a quick response to "Проверить"
    taps it again -- without serializing per user_id, each tap started its
    own full provider round for the SAME external account concurrently,
    piling load onto an already-slow tgrass call instead of just waiting
    for the first round. Confirmed live: responses up to 44s and Telegram
    callback queries expiring before an answer could even be sent."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        patcher = patch.object(sponsor_wall_settings, "tgrass_code", "code")
        patcher.start()
        self.addCleanup(patcher.stop)
        for attr in ("botohub_key", "traffy_key", "flyerhub_op_key"):
            p = patch.object(sponsor_wall_settings, attr, "")
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_two_concurrent_calls_for_the_same_user_never_overlap(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U"))
            await session.commit()

        in_flight = 0
        peak_concurrency = 0
        lock = asyncio.Lock()

        async def slow_tgrass(*_args, **_kwargs):
            nonlocal in_flight, peak_concurrency
            async with lock:
                in_flight += 1
                peak_concurrency = max(peak_concurrency, in_flight)
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1
            return []

        async def run_once():
            async with self.sessions() as session:
                user = await session.get(User, 1)
                await evaluate_provider_wave(inner(1), user, session, bot=None)

        with (
            patch("bot.services.tgrass.check_tgrass", AsyncMock(side_effect=slow_tgrass)),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            await asyncio.gather(run_once(), run_once())

        self.assertEqual(peak_concurrency, 1)

    async def test_two_different_users_run_concurrently_not_serialized(self) -> None:
        """The lock is per user_id -- it must not accidentally serialize
        unrelated users onto one global lock."""
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="U"))
            session.add(User(user_id=2, first_name="U2"))
            await session.commit()

        in_flight = 0
        peak_concurrency = 0
        lock = asyncio.Lock()

        async def slow_tgrass(*_args, **_kwargs):
            nonlocal in_flight, peak_concurrency
            async with lock:
                in_flight += 1
                peak_concurrency = max(peak_concurrency, in_flight)
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1
            return []

        async def run_once(uid: int):
            async with self.sessions() as session:
                user = await session.get(User, uid)
                await evaluate_provider_wave(inner(uid), user, session, bot=None)

        with (
            patch("bot.services.tgrass.check_tgrass", AsyncMock(side_effect=slow_tgrass)),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            await asyncio.gather(run_once(1), run_once(2))

        self.assertEqual(peak_concurrency, 2)


if __name__ == "__main__":
    unittest.main()
