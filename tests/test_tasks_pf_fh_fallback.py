import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.tasks import _show_next_task, _show_pf_task, _show_fh_task, settings


def pf_sponsor(name: str) -> dict:
    return {"name": name, "link": f"https://t.me/{name.lower()}"}


def fh_sponsor(name: str) -> dict:
    return {"name": name, "signature": f"sig-{name.lower()}", "links": [f"https://example.com/{name.lower()}"]}


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), delete=AsyncMock()),
    )


def db_user(completed: int = 0) -> SimpleNamespace:
    return SimpleNamespace(user_id=1, tasks_completed_count=completed)


class TaskFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_even_count_tries_piarflow_first(self) -> None:
        cb = callback()
        with (
            patch("bot.handlers.tasks.TaskRepository.all_active", AsyncMock(return_value=[])),
            patch("bot.handlers.tasks.TaskRepository.completed_ids", AsyncMock(return_value=set())),
            patch.object(settings, "piarflow_key", "pf-key"),
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", ""),
            patch("bot.handlers.tasks.get_sponsors", AsyncMock(return_value=[pf_sponsor("A")])) as get_sponsors,
            patch("bot.handlers.tasks.fh_get_tasks", AsyncMock()) as fh_get_tasks,
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.SettingsRepository.get_int", AsyncMock(return_value=100)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
            patch("bot.handlers.tasks.SettingsRepository.set", AsyncMock()),
        ):
            await _show_next_task(cb, db_user(completed=0), SimpleNamespace())

        get_sponsors.assert_awaited_once()
        fh_get_tasks.assert_not_awaited()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("A", rendered)

    async def test_odd_count_tries_flyerhub_first(self) -> None:
        cb = callback()
        with (
            patch("bot.handlers.tasks.TaskRepository.all_active", AsyncMock(return_value=[])),
            patch("bot.handlers.tasks.TaskRepository.completed_ids", AsyncMock(return_value=set())),
            patch.object(settings, "piarflow_key", "pf-key"),
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", ""),
            patch("bot.handlers.tasks.get_sponsors", AsyncMock()) as get_sponsors,
            patch("bot.handlers.tasks.fh_get_tasks", AsyncMock(return_value=[fh_sponsor("B")])) as fh_get_tasks,
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
        ):
            await _show_next_task(cb, db_user(completed=1), SimpleNamespace())

        fh_get_tasks.assert_awaited_once()
        get_sponsors.assert_not_awaited()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("B", rendered)

    async def test_piarflow_empty_falls_back_to_flyerhub(self) -> None:
        cb = callback()
        with (
            patch("bot.handlers.tasks.TaskRepository.all_active", AsyncMock(return_value=[])),
            patch("bot.handlers.tasks.TaskRepository.completed_ids", AsyncMock(return_value=set())),
            patch.object(settings, "piarflow_key", "pf-key"),
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", ""),
            patch("bot.handlers.tasks.get_sponsors", AsyncMock(return_value=[])) as get_sponsors,
            patch("bot.handlers.tasks.fh_get_tasks", AsyncMock(return_value=[fh_sponsor("Rescue")])) as fh_get_tasks,
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.SettingsRepository.get_int", AsyncMock(return_value=100)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
        ):
            await _show_next_task(cb, db_user(completed=0), SimpleNamespace())

        get_sponsors.assert_awaited_once()
        fh_get_tasks.assert_awaited_once()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Rescue", rendered)

    async def test_flyerhub_empty_falls_back_to_piarflow(self) -> None:
        cb = callback()
        with (
            patch("bot.handlers.tasks.TaskRepository.all_active", AsyncMock(return_value=[])),
            patch("bot.handlers.tasks.TaskRepository.completed_ids", AsyncMock(return_value=set())),
            patch.object(settings, "piarflow_key", "pf-key"),
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", ""),
            patch("bot.handlers.tasks.get_sponsors", AsyncMock(return_value=[pf_sponsor("Rescue2")])) as get_sponsors,
            patch("bot.handlers.tasks.fh_get_tasks", AsyncMock(return_value=[])) as fh_get_tasks,
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.SettingsRepository.get_int", AsyncMock(return_value=100)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
            patch("bot.handlers.tasks.SettingsRepository.set", AsyncMock()),
        ):
            await _show_next_task(cb, db_user(completed=1), SimpleNamespace())

        fh_get_tasks.assert_awaited_once()
        get_sponsors.assert_awaited_once()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Rescue2", rendered)

    async def test_both_empty_shows_all_done_without_infinite_bounce(self) -> None:
        cb = callback()
        with (
            patch("bot.handlers.tasks.TaskRepository.all_active", AsyncMock(return_value=[])),
            patch("bot.handlers.tasks.TaskRepository.completed_ids", AsyncMock(return_value=set())),
            patch.object(settings, "piarflow_key", "pf-key"),
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", ""),
            patch("bot.handlers.tasks.get_sponsors", AsyncMock(return_value=[])) as get_sponsors,
            patch("bot.handlers.tasks.fh_get_tasks", AsyncMock(return_value=[])) as fh_get_tasks,
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.SettingsRepository.get_int", AsyncMock(return_value=100)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
        ):
            await _show_next_task(cb, db_user(completed=0), SimpleNamespace())

        # exactly one call each — no bouncing back and forth
        get_sponsors.assert_awaited_once()
        fh_get_tasks.assert_awaited_once()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Все задания выполнены", rendered)

    async def test_only_flyerhub_configured_is_reachable_directly(self) -> None:
        cb = callback()
        with (
            patch("bot.handlers.tasks.TaskRepository.all_active", AsyncMock(return_value=[])),
            patch("bot.handlers.tasks.TaskRepository.completed_ids", AsyncMock(return_value=set())),
            patch.object(settings, "piarflow_key", ""),
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", ""),
            patch("bot.handlers.tasks.fh_get_tasks", AsyncMock(return_value=[fh_sponsor("OnlyFH")])) as fh_get_tasks,
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
        ):
            await _show_next_task(cb, db_user(completed=0), SimpleNamespace())

        fh_get_tasks.assert_awaited_once()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("OnlyFH", rendered)


if __name__ == "__main__":
    unittest.main()
