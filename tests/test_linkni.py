import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.tasks import (
    _show_pf_task,
    _show_tasks_exhausted_screen,
    _try_show_linkni,
    cb_linkni_check,
    cb_linkni_skip,
    settings,
)
from bot.services.linkni import check_linkni_subscription, linkni_link


class _Response:
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return self.payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, *_args, **_kwargs):
        return self.response


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
    )


def db_user(completed: int = 0, balance: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(user_id=1, tasks_completed_count=completed, stars_balance=balance)


class CheckLinkniSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def _check(self, response: _Response) -> str | None:
        with patch("bot.services.linkni.aiohttp.ClientSession", return_value=_Session(response)):
            return await check_linkni_subscription("2o2i", 123)

    async def test_no_code_returns_none(self) -> None:
        self.assertIsNone(await check_linkni_subscription("", 123))

    async def test_http_error_returns_none(self) -> None:
        self.assertIsNone(await self._check(_Response(500, [])))

    async def test_empty_list_returns_none(self) -> None:
        self.assertIsNone(await self._check(_Response(200, [])))

    async def test_picks_latest_status_by_timestamp(self) -> None:
        status = await self._check(
            _Response(
                200,
                [
                    {"user_id": 123, "status": "not_subscribed", "timestamp": "2026-04-02T10:00:00Z"},
                    {"user_id": 123, "status": "subscribed", "timestamp": "2026-04-02T12:00:00Z"},
                ],
            )
        )
        self.assertEqual(status, "subscribed")

    def test_link_includes_code_and_fixed_sub_code(self) -> None:
        self.assertEqual(
            linkni_link("2o2i"),
            "https://telegram.me/linknibot/app?startapp=x_2o2i_tasks",
        )


class LinkniTaskFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_try_show_linkni_skips_when_not_configured(self) -> None:
        cb = callback()
        with patch.object(settings, "linkni_code", ""):
            shown = await _try_show_linkni(cb, db_user(), SimpleNamespace())
        self.assertFalse(shown)
        cb.message.edit_text.assert_not_awaited()

    async def test_try_show_linkni_skips_when_already_done(self) -> None:
        cb = callback()
        with (
            patch.object(settings, "linkni_code", "2o2i"),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="1")),
        ):
            shown = await _try_show_linkni(cb, db_user(), SimpleNamespace())
        self.assertFalse(shown)

    async def test_try_show_linkni_renders_when_pending(self) -> None:
        cb = callback()
        with (
            patch.object(settings, "linkni_code", "2o2i"),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
        ):
            shown = await _try_show_linkni(cb, db_user(), SimpleNamespace())
        self.assertTrue(shown)
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
        self.assertIn("https://telegram.me/linknibot/app?startapp=x_2o2i_tasks", urls)

    async def test_check_subscribed_pays_reward_once(self) -> None:
        cb = callback()
        user = db_user(balance=0.0)
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(settings, "linkni_code", "2o2i"),
            patch("bot.handlers.tasks.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
            patch("bot.handlers.tasks.SettingsRepository.set", AsyncMock()) as set_flag,
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.check_linkni_subscription", AsyncMock(return_value="subscribed")),
            patch("bot.handlers.tasks.check_referral_reward", AsyncMock()) as check_reward,
        ):
            await cb_linkni_check(cb, user, session, bot=SimpleNamespace())

        self.assertAlmostEqual(float(user.stars_balance), 0.3)
        self.assertEqual(user.tasks_completed_count, 1)
        set_flag.assert_awaited_once_with("linkni_done:1", "1")
        check_reward.assert_awaited_once()

    async def test_check_no_sponsors_marks_done_without_paying(self) -> None:
        cb = callback()
        user = db_user(balance=0.0)
        with (
            patch.object(settings, "linkni_code", "2o2i"),
            patch("bot.handlers.tasks.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
            patch("bot.handlers.tasks.SettingsRepository.set", AsyncMock()) as set_flag,
            patch("bot.handlers.tasks.check_linkni_subscription", AsyncMock(return_value="no_sponsors")),
        ):
            await cb_linkni_check(cb, user, SimpleNamespace(), bot=SimpleNamespace())

        self.assertEqual(float(user.stars_balance), 0.0)
        set_flag.assert_awaited_once_with("linkni_done:1", "1")

    async def test_check_not_subscribed_does_not_mark_done(self) -> None:
        cb = callback()
        user = db_user(balance=0.0)
        with (
            patch.object(settings, "linkni_code", "2o2i"),
            patch("bot.handlers.tasks.SettingsRepository.get_bool", AsyncMock(return_value=True)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
            patch("bot.handlers.tasks.SettingsRepository.set", AsyncMock()) as set_flag,
            patch("bot.handlers.tasks.check_linkni_subscription", AsyncMock(return_value="not_subscribed")),
        ):
            await cb_linkni_check(cb, user, SimpleNamespace(), bot=SimpleNamespace())

        set_flag.assert_not_awaited()
        self.assertEqual(float(user.stars_balance), 0.0)

    async def test_skip_shows_exhausted_screen(self) -> None:
        cb = callback()
        with patch("bot.handlers.tasks.SettingsRepository.get_bool", AsyncMock(return_value=True)):
            await cb_linkni_skip(cb, db_user(completed=3), SimpleNamespace())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Все задания выполнены", rendered)
        self.assertIn("3", rendered)

    async def test_piarflow_exhausted_skips_fh_webapp_hop_straight_to_linkni(self) -> None:
        """When FlyerHub is in webapp mode, it's just a static screen the
        user already sees on its own alternation turn — cross-falling-back
        there again from an exhausted PiarFlow list only buries linkni
        behind an extra manual skip. PiarFlow running dry should go
        straight to linkni instead."""
        cb = callback()
        with (
            patch.object(settings, "piarflow_key", "pf-key"),
            patch.object(settings, "flyerhub_key", "fh-key"),
            patch.object(settings, "flyerhub_webapp_url", "https://telegram.me/FlyWebTasksBot/app?startapp=abc"),
            patch.object(settings, "linkni_code", "2o2i"),
            patch("bot.handlers.tasks.get_sponsors", AsyncMock(return_value=[])),
            patch("bot.handlers.tasks.SettingsRepository.get_float", AsyncMock(return_value=0.3)),
            patch("bot.handlers.tasks.SettingsRepository.get_int", AsyncMock(return_value=100)),
            patch("bot.handlers.tasks.SettingsRepository.get", AsyncMock(return_value="")),
            patch("bot.handlers.tasks._show_fh_webapp", AsyncMock()) as show_fh_webapp,
        ):
            await _show_pf_task(cb, db_user(), SimpleNamespace())

        show_fh_webapp.assert_not_awaited()
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
        self.assertIn("https://telegram.me/linknibot/app?startapp=x_2o2i_tasks", urls)


if __name__ == "__main__":
    unittest.main()
