import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.games import cb_games_menu
from bot.handlers.tasks import _show_next_task
from bot.handlers.top import cb_top_menu
from bot.handlers.withdraw import cb_withdraw_menu

# Patching ContentRepository.get (not get_text) lets the real get_text()
# fallback run, so these tests exercise the actual DEFAULT_TEXTS templates
# rather than a hand-picked stand-in string.
_NO_STORED_CONTENT = patch("bot.database.repositories.content.ContentRepository.get", AsyncMock(return_value=None))

_LEFTOVER_PLACEHOLDER = re.compile(r"\{[a-zA-Z_]+\}")


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(
            delete=AsyncMock(),
            answer_photo=AsyncMock(),
            edit_text=AsyncMock(),
            answer=AsyncMock(),
        ),
    )


class ContentRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_games_menu_renders_without_leftover_placeholders(self) -> None:
        cb = callback()
        db_user = SimpleNamespace(user_id=1, referrals_count=10, stars_balance=5.5)
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), clear=AsyncMock())
        with (
            _NO_STORED_CONTENT,
            patch("bot.handlers.games.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.games.SettingsRepository.get_int", AsyncMock(return_value=3)),
            patch("bot.handlers.games.SettingsRepository.get_float", AsyncMock(return_value=1.5)),
            patch("bot.handlers.games.ContentRepository.get_photo", AsyncMock(return_value=None)),
        ):
            await cb_games_menu(cb, SimpleNamespace(), db_user, state)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertFalse(_LEFTOVER_PLACEHOLDER.search(rendered), rendered)
        self.assertIn("5.50", rendered)

    async def test_withdraw_menu_renders_without_leftover_placeholders(self) -> None:
        cb = callback()
        db_user = SimpleNamespace(user_id=1, username="tester", stars_balance=12.34)
        state = SimpleNamespace(clear=AsyncMock())
        with (
            _NO_STORED_CONTENT,
            patch("bot.handlers.withdraw.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.withdraw.SettingsRepository.get", AsyncMock(return_value="15,25,50")),
            patch("bot.handlers.withdraw.SettingsRepository.get_float", AsyncMock(return_value=15.0)),
            patch("bot.handlers.withdraw.ContentRepository.get_photo", AsyncMock(return_value=None)),
        ):
            await cb_withdraw_menu(cb, db_user, SimpleNamespace(), state)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertFalse(_LEFTOVER_PLACEHOLDER.search(rendered), rendered)
        self.assertIn("12.34", rendered)

    async def test_tasks_all_done_fallback_renders_without_leftover_placeholders(self) -> None:
        cb = callback()
        db_user = SimpleNamespace(user_id=1, tasks_completed_count=4)
        with (
            _NO_STORED_CONTENT,
            patch("bot.handlers.tasks.TaskRepository.all_active", AsyncMock(return_value=[])),
            patch("bot.handlers.tasks.TaskRepository.completed_ids", AsyncMock(return_value=set())),
            patch("bot.handlers.tasks.settings.piarflow_key", ""),
            patch("bot.handlers.tasks.settings.flyerhub_key", ""),
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
        ):
            await _show_next_task(cb, db_user, SimpleNamespace())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertFalse(_LEFTOVER_PLACEHOLDER.search(rendered), rendered)
        self.assertIn("0.3", rendered)

    async def test_top_menu_renders_without_leftover_placeholders(self) -> None:
        cb = callback()
        with (
            _NO_STORED_CONTENT,
            patch("bot.handlers.top.ContentRepository.get_photo", AsyncMock(return_value=None)),
        ):
            await cb_top_menu(cb, SimpleNamespace(), SimpleNamespace())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertFalse(_LEFTOVER_PLACEHOLDER.search(rendered), rendered)


if __name__ == "__main__":
    unittest.main()
