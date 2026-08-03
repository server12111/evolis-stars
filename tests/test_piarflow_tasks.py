import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.tasks import _show_pf_task, cb_pf_task_check
from bot.keyboards.tasks import pf_task_id


def sponsor(name: str) -> dict:
    return {
        "name": name,
        "link": f"https://t.me/{name.lower()}",
    }


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(
            edit_text=AsyncMock(),
            answer=AsyncMock(),
        )
    )


class PiarFlowNextTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefetched_next_task_is_shown_without_refetch(self) -> None:
        cb = callback()
        db_user = SimpleNamespace(user_id=1, tasks_completed_count=1)
        next_task = sponsor("Next")

        with (
            patch(
                "bot.handlers.tasks.SettingsRepository.get_float",
                AsyncMock(return_value=0.3),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.get_int",
                AsyncMock(return_value=100),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.get",
                AsyncMock(return_value=""),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.set",
                AsyncMock(),
            ),
            patch(
                "bot.handlers.tasks.get_sponsors",
                AsyncMock(),
            ) as get_sponsors,
        ):
            await _show_pf_task(
                cb,
                db_user,
                SimpleNamespace(),
                pf_tasks=[next_task],
                retry_if_empty=True,
            )

        get_sponsors.assert_not_awaited()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Next", rendered)
        self.assertNotIn("Все задания выполнены", rendered)

    async def test_empty_transition_retries_and_shows_new_task(self) -> None:
        cb = callback()
        db_user = SimpleNamespace(user_id=1, tasks_completed_count=1)
        next_task = sponsor("AfterRetry")

        with (
            patch(
                "bot.handlers.tasks.SettingsRepository.get_float",
                AsyncMock(return_value=0.3),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.get_int",
                AsyncMock(return_value=100),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.get",
                AsyncMock(return_value=""),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.set",
                AsyncMock(),
            ),
            patch(
                "bot.handlers.tasks.get_sponsors",
                AsyncMock(side_effect=[[], [next_task]]),
            ) as get_sponsors,
            patch("bot.handlers.tasks.asyncio.sleep", AsyncMock()),
        ):
            await _show_pf_task(
                cb,
                db_user,
                SimpleNamespace(),
                pf_tasks=[],
                retry_if_empty=True,
            )

        self.assertEqual(get_sponsors.await_count, 2)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("AfterRetry", rendered)
        self.assertNotIn("Все задания выполнены", rendered)

    async def test_api_error_shows_exhausted_screen_not_a_service_error(self) -> None:
        """A real PiarFlow failure must read the same as "nothing left" —
        never as an alarming error — so a transient outage doesn't look like
        a bug to the user."""
        cb = callback()
        db_user = SimpleNamespace(user_id=1, tasks_completed_count=1)

        with (
            patch(
                "bot.handlers.tasks.SettingsRepository.get_float",
                AsyncMock(return_value=0.3),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.get_int",
                AsyncMock(return_value=100),
            ),
            patch(
                "bot.handlers.tasks.get_sponsors",
                AsyncMock(return_value=None),
            ),
        ):
            await _show_pf_task(cb, db_user, SimpleNamespace())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Все задания выполнены", rendered)
        self.assertNotIn("временно не ответил", rendered)

    async def test_reward_requires_explicit_check_even_if_task_list_is_empty(self) -> None:
        cb = callback()
        link = "https://t.me/required_channel"
        link_id = pf_task_id(link)
        cb.data = f"pf_task:check:{link_id}"
        db_user = SimpleNamespace(
            user_id=1,
            stars_balance=0.0,
            tasks_completed_count=0,
        )
        session = SimpleNamespace(commit=AsyncMock())

        async def setting_value(_self, key: str, default: str = "") -> str:
            if key.startswith("pf_link:"):
                return link
            return default

        with (
            patch("bot.handlers.tasks.settings.piarflow_key", "configured"),
            patch(
                "bot.handlers.tasks.SettingsRepository.get",
                setting_value,
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.get_int",
                AsyncMock(return_value=100),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.get_float",
                AsyncMock(return_value=0.3),
            ),
            patch(
                "bot.handlers.tasks.SettingsRepository.stage",
                AsyncMock(),
            ) as stage,
            patch(
                "bot.handlers.tasks.check_sponsors",
                AsyncMock(return_value=False),
            ) as check,
            patch(
                "bot.handlers.tasks.get_sponsors",
                AsyncMock(return_value=[]),
            ) as get_sponsors,
        ):
            await cb_pf_task_check(
                cb,
                db_user,
                session,
                SimpleNamespace(),
            )

        check.assert_awaited_once_with("configured", 1, [link])
        get_sponsors.assert_not_awaited()
        stage.assert_not_awaited()
        session.commit.assert_not_awaited()
        self.assertEqual(db_user.stars_balance, 0.0)
        self.assertEqual(db_user.tasks_completed_count, 0)


if __name__ == "__main__":
    unittest.main()
