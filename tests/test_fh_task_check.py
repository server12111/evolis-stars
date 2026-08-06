import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.tasks import cb_fh_task_check, settings


def callback(signature: str = "sig-1") -> SimpleNamespace:
    return SimpleNamespace(
        data=f"fh_task:check:{signature}",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
    )


def db_user(completed: int = 0, balance: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(user_id=1, tasks_completed_count=completed, stars_balance=balance)


class FhTaskCheckStatusTests(unittest.IsolatedAsyncioTestCase):
    """FlyerHub's check_task can return 'waiting' — an unconfirmed
    anti-fraud hold — distinct from 'complete'. Paying/locking on
    'waiting' would let a user defeat that hold just by tapping the
    check button once, without actually finishing the sponsor's task."""

    async def _run(self, status: str):
        stored: dict[str, str] = {}

        async def fake_get(self, key, default=""):
            return stored.get(key, default)

        async def fake_set(self, key, value):
            stored[key] = value

        cb = callback()
        session = SimpleNamespace(commit=AsyncMock())
        user = db_user()
        with (
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.database.repositories.settings.SettingsRepository.get", fake_get),
            patch("bot.database.repositories.settings.SettingsRepository.set", fake_set),
            patch("bot.database.repositories.settings.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.fh_check_task", AsyncMock(return_value=status)),
            patch("bot.handlers.tasks.check_referral_reward", AsyncMock()) as check_reward,
            patch("bot.handlers.tasks._show_fh_task", AsyncMock()) as show_task,
        ):
            await cb_fh_task_check(cb, user, session, bot=SimpleNamespace())
        return user, stored, check_reward, show_task

    async def test_waiting_status_does_not_pay_or_lock(self) -> None:
        user, stored, check_reward, show_task = await self._run("waiting")
        self.assertEqual(float(user.stars_balance), 1.0)
        self.assertEqual(user.tasks_completed_count, 0)
        self.assertNotIn("fh_done:1:sig-1", stored)
        check_reward.assert_not_awaited()
        show_task.assert_awaited_once()

    async def test_complete_status_pays_and_locks(self) -> None:
        user, stored, check_reward, show_task = await self._run("complete")
        self.assertAlmostEqual(float(user.stars_balance), 1.3)
        self.assertEqual(user.tasks_completed_count, 1)
        self.assertEqual(stored.get("fh_done:1:sig-1"), "1")
        check_reward.assert_awaited_once()

    async def test_incomplete_status_does_not_pay(self) -> None:
        user, stored, check_reward, show_task = await self._run("incomplete")
        self.assertEqual(float(user.stars_balance), 1.0)
        self.assertEqual(user.tasks_completed_count, 0)
        self.assertNotIn("fh_done:1:sig-1", stored)
        check_reward.assert_not_awaited()

    async def test_waiting_then_complete_still_pays_exactly_once(self) -> None:
        """A user who checks while still 'waiting' and checks again later
        once it's genuinely 'complete' must be paid exactly once."""
        stored: dict[str, str] = {}

        async def fake_get(self, key, default=""):
            return stored.get(key, default)

        async def fake_set(self, key, value):
            stored[key] = value

        user = db_user()
        session = SimpleNamespace(commit=AsyncMock())

        with (
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.database.repositories.settings.SettingsRepository.get", fake_get),
            patch("bot.database.repositories.settings.SettingsRepository.set", fake_set),
            patch("bot.database.repositories.settings.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.fh_check_task", AsyncMock(return_value="waiting")),
            patch("bot.handlers.tasks.check_referral_reward", AsyncMock()),
            patch("bot.handlers.tasks._show_fh_task", AsyncMock()),
        ):
            await cb_fh_task_check(callback(), user, session, bot=SimpleNamespace())

        self.assertEqual(float(user.stars_balance), 1.0)

        with (
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.database.repositories.settings.SettingsRepository.get", fake_get),
            patch("bot.database.repositories.settings.SettingsRepository.set", fake_set),
            patch("bot.database.repositories.settings.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.fh_check_task", AsyncMock(return_value="complete")),
            patch("bot.handlers.tasks.check_referral_reward", AsyncMock()),
            patch("bot.handlers.tasks._show_fh_task", AsyncMock()),
        ):
            await cb_fh_task_check(callback(), user, session, bot=SimpleNamespace())

        self.assertAlmostEqual(float(user.stars_balance), 1.3)
        self.assertEqual(user.tasks_completed_count, 1)


if __name__ == "__main__":
    unittest.main()
