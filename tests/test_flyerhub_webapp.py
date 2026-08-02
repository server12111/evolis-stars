import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.tasks import _show_fh_task, cb_fh_webapp_check, cb_fh_webapp_skip, settings


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), delete=AsyncMock()),
    )


def db_user(completed: int = 0, balance: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(user_id=1, tasks_completed_count=completed, stars_balance=balance)


class FlyerHubWebappTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_fh_task_uses_webapp_flow_when_url_configured(self) -> None:
        cb = callback()
        with (
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", "https://telegram.me/FlyWebTasksBot/app?startapp=abc"),
            patch(
                "bot.handlers.tasks.fh_get_completed_tasks",
                AsyncMock(return_value={"completed_tasks": [], "count_all_tasks": 3}),
            ),
            patch("bot.handlers.tasks.fh_get_tasks", AsyncMock()) as fh_get_tasks,
        ):
            await _show_fh_task(cb, db_user(), SimpleNamespace())

        fh_get_tasks.assert_not_awaited()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Доступно заданий: <b>3</b>", rendered)

    async def test_webapp_zero_tasks_still_offers_the_webapp_link(self) -> None:
        """count_all_tasks is 0 for anyone who hasn't opened the Mini App
        yet — it must never be read as "nothing to do"."""
        cb = callback()
        with (
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", "https://telegram.me/FlyWebTasksBot/app?startapp=abc"),
            patch(
                "bot.handlers.tasks.fh_get_completed_tasks",
                AsyncMock(return_value={"completed_tasks": [], "count_all_tasks": 0}),
            ),
        ):
            await _show_fh_task(cb, db_user(), SimpleNamespace())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertNotIn("Доступно заданий", rendered)
        self.assertNotIn("FlyerHub", rendered)
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
        self.assertIn("https://telegram.me/FlyWebTasksBot/app?startapp=abc", urls)

    async def test_webapp_unavailable_shows_retry(self) -> None:
        cb = callback()
        with (
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", "https://telegram.me/FlyWebTasksBot/app?startapp=abc"),
            patch("bot.handlers.tasks.fh_get_completed_tasks", AsyncMock(return_value=None)),
        ):
            await _show_fh_task(cb, db_user(), SimpleNamespace())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("не ответил", rendered)

    async def test_check_credits_each_new_completed_signature_once(self) -> None:
        cb = callback()
        session = SimpleNamespace(commit=AsyncMock())
        stored: dict[str, str] = {}

        async def fake_get(self, key, default=""):
            return stored.get(key, default)

        async def fake_set(self, key, value):
            stored[key] = value

        user = db_user(balance=1.0)
        with (
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", "https://telegram.me/FlyWebTasksBot/app?startapp=abc"),
            patch(
                "bot.handlers.tasks.fh_get_completed_tasks",
                AsyncMock(
                    side_effect=[
                        {
                            "completed_tasks": [
                                {"signature": "sig-1", "task": "subscribe channel", "price": 5, "status": "ok"},
                                {"signature": "sig-2", "task": "follow link", "price": 3, "status": "ok"},
                            ],
                            "count_all_tasks": 2,
                        },
                        {"completed_tasks": [], "count_all_tasks": 2},
                    ]
                ),
            ),
            patch("bot.database.repositories.settings.SettingsRepository.get", fake_get),
            patch("bot.database.repositories.settings.SettingsRepository.set", fake_set),
            patch("bot.database.repositories.settings.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.check_referral_reward", AsyncMock()) as check_reward,
        ):
            await cb_fh_webapp_check(cb, user, session, bot=SimpleNamespace())

        # 2 new completions * 0.3 reward = 0.6, on top of the starting 1.0 balance.
        self.assertAlmostEqual(float(user.stars_balance), 1.6)
        self.assertEqual(user.tasks_completed_count, 2)
        check_reward.assert_awaited_once()
        self.assertEqual(stored.get("fh_done:1:sig-1"), "1")
        self.assertEqual(stored.get("fh_done:1:sig-2"), "1")

        # Calling again with the same 2 signatures already recorded pays nothing new.
        cb2 = callback()
        with (
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", "https://telegram.me/FlyWebTasksBot/app?startapp=abc"),
            patch(
                "bot.handlers.tasks.fh_get_completed_tasks",
                AsyncMock(
                    return_value={
                        "completed_tasks": [
                            {"signature": "sig-1", "task": "subscribe channel", "price": 5, "status": "ok"},
                            {"signature": "sig-2", "task": "follow link", "price": 3, "status": "ok"},
                        ],
                        "count_all_tasks": 2,
                    }
                ),
            ),
            patch("bot.database.repositories.settings.SettingsRepository.get", fake_get),
            patch("bot.database.repositories.settings.SettingsRepository.set", fake_set),
            patch("bot.database.repositories.settings.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.check_referral_reward", AsyncMock()) as check_reward2,
        ):
            await cb_fh_webapp_check(cb2, user, session, bot=SimpleNamespace())

        self.assertAlmostEqual(float(user.stars_balance), 1.6)
        self.assertEqual(user.tasks_completed_count, 2)
        check_reward2.assert_not_awaited()

    async def test_skip_falls_back_to_piarflow_when_configured(self) -> None:
        cb = callback()
        with (
            patch.object(settings, "piarflow_key", "pf-key"),
            patch("bot.handlers.tasks.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.tasks._show_pf_task", AsyncMock()) as show_pf,
        ):
            await cb_fh_webapp_skip(cb, db_user(), SimpleNamespace())

        show_pf.assert_awaited_once()
        self.assertTrue(show_pf.await_args.kwargs.get("tried_other"))

    async def test_skip_shows_all_done_when_no_piarflow(self) -> None:
        cb = callback()
        with (
            patch.object(settings, "piarflow_key", ""),
            patch("bot.handlers.tasks.SettingsRepository.get_bool", AsyncMock(return_value=True)),
        ):
            await cb_fh_webapp_skip(cb, db_user(), SimpleNamespace())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Все задания выполнены", rendered)


if __name__ == "__main__":
    unittest.main()
