import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.middlewares.sponsor_wall import _evaluate_wave_state, run_sponsor_wall_check, settings


def offers(prefix: str, count: int) -> list[dict]:
    return [
        {"name": f"Channel {i}", "url": f"https://t.me/{prefix}{i}"}
        for i in range(count)
    ]


def inner() -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(
            is_premium=False, username="u", language_code="ru", first_name="U",
        ),
        message=None,
        answer=AsyncMock(),
    )


def user(**kwargs) -> SimpleNamespace:
    base = dict(
        user_id=1,
        sponsor_wave=0,
        sponsor_wave_one=None,
        sponsor_wave_two=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def fake_session() -> SimpleNamespace:
    """Minimal fake covering both the direct commit() calls this suite
    already exercised and BlockedSponsorRepository's SELECT (via
    _evaluate_wave_state's blocklist lookup) -- defaults to an empty
    blocklist so existing scenarios are unaffected."""
    return SimpleNamespace(
        commit=AsyncMock(),
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))),
    )


def frozen_user(items: list[dict], **kwargs) -> SimpleNamespace:
    base = dict(
        user_id=1,
        sponsor_wave=1,
        sponsor_wave_one=json.dumps(items),
        sponsor_wave_two=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def fake_get_int(wave_size: int = 10):
    async def _get_int(self, key: str, default: int = 0) -> int:
        return {"sponsor_max_channels": wave_size}.get(key, default)

    return _get_int


class TraffyFreshWaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_traffy_tasks_called_when_not_yet_frozen(self) -> None:
        session = fake_session()
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", "traffy-key"),
            patch.object(settings, "flyerhub_op_key", ""),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch(
                "bot.services.traffy.get_traffy_tasks",
                AsyncMock(return_value=offers("traffy", 2)),
            ) as get_tasks,
        ):
            state = await _evaluate_wave_state(inner(), user(), session)

        get_tasks.assert_awaited_once()
        self.assertEqual(get_tasks.await_args.kwargs.get("limit"), 10)
        self.assertEqual(state.status, "pending")
        self.assertEqual(len(state.items or []), 2)

    async def test_traffy_not_configured_contributes_nothing(self) -> None:
        session = fake_session()
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", ""),
            patch.object(settings, "flyerhub_op_key", ""),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 1))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.traffy.get_traffy_tasks", AsyncMock()) as get_tasks,
        ):
            state = await _evaluate_wave_state(inner(), user(), session)

        get_tasks.assert_not_awaited()
        self.assertEqual(state.status, "pending")
        self.assertEqual(len(state.items or []), 1)


class TraffyRecheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_wave_rechecks_by_assignment_id_not_a_fresh_fetch(self) -> None:
        """Re-fetching /tasks on every "check" press would hand out brand
        new assignment_ids and silently replace the frozen wave -- the
        recheck must go through check_traffy_tasks with the SAVED ref."""
        session = fake_session()
        frozen = frozen_user(
            [{"provider": "traffy", "url": "https://t.me/tf0", "name": "TF", "ref": "assign-0"}]
        )
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", "traffy-key"),
            patch.object(settings, "flyerhub_op_key", ""),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.traffy.get_traffy_tasks", AsyncMock()) as get_tasks,
            patch(
                "bot.services.traffy.check_traffy_tasks",
                AsyncMock(return_value={"assign-0": True}),
            ) as check_tasks,
        ):
            state = await _evaluate_wave_state(inner(), frozen, session)

        get_tasks.assert_not_awaited()
        check_tasks.assert_awaited_once_with("traffy-key", 1, ["assign-0"])
        self.assertEqual(state.status, "complete")

    async def test_frozen_wave_stays_pending_when_not_completed(self) -> None:
        session = fake_session()
        frozen = frozen_user(
            [{"provider": "traffy", "url": "https://t.me/tf0", "name": "TF", "ref": "assign-0"}]
        )
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", "traffy-key"),
            patch.object(settings, "flyerhub_op_key", ""),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch(
                "bot.services.traffy.check_traffy_tasks",
                AsyncMock(return_value={"assign-0": False}),
            ),
        ):
            state = await _evaluate_wave_state(inner(), frozen, session)

        self.assertEqual(state.status, "pending")

    async def test_check_failure_reports_unavailable_not_complete(self) -> None:
        session = fake_session()
        frozen = frozen_user(
            [{"provider": "traffy", "url": "https://t.me/tf0", "name": "TF", "ref": "assign-0"}]
        )
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", "traffy-key"),
            patch.object(settings, "flyerhub_op_key", ""),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.traffy.check_traffy_tasks", AsyncMock(return_value=None)),
        ):
            state = await _evaluate_wave_state(inner(), frozen, session)

        self.assertEqual(state.status, "unavailable")


class FlyerhubOpKeyIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_op_wall_uses_flyerhub_op_key_not_the_tasks_key(self) -> None:
        """A webapp-type FLYERHUB_KEY (used by "Задания") rejects /get_tasks
        outright -- the ОП wall must use the separate FLYERHUB_OP_KEY."""
        session = fake_session()
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", ""),
            patch.object(settings, "flyerhub_key", "tasks-key"),
            patch.object(settings, "flyerhub_op_key", "op-only-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.flyerhub.fh_get_tasks_op", AsyncMock(return_value=[])) as get_tasks,
        ):
            await _evaluate_wave_state(inner(), user(), session)

        get_tasks.assert_awaited_once()
        self.assertEqual(get_tasks.await_args.args[0], "op-only-key")

    async def test_not_configured_when_only_tasks_key_is_set(self) -> None:
        session = fake_session()
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", ""),
            patch.object(settings, "flyerhub_key", "tasks-key"),
            patch.object(settings, "flyerhub_op_key", ""),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 1))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.flyerhub.fh_get_tasks_op", AsyncMock()) as get_tasks,
        ):
            state = await _evaluate_wave_state(inner(), user(), session)

        get_tasks.assert_not_awaited()
        self.assertEqual(state.status, "pending")


class FlyerhubTrustKindTests(unittest.IsolatedAsyncioTestCase):
    async def test_trust_kind_item_is_not_cleared_by_live_membership_even_if_member(self) -> None:
        """A FlyerHub "give boost" task decorates to kind="trust". Even
        though our own bot sees the user as a channel member, membership
        isn't the same as having given the boost -- only FlyerHub's own
        check_task verdict may resolve it."""
        session = fake_session()
        frozen = frozen_user([{
            "provider": "flyerhub", "url": "https://t.me/boostchan", "name": "Boost",
            "ref": "sig-1", "kind": "trust",
        }])
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", ""),
            patch.object(settings, "flyerhub_op_key", "op-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.flyerhub.fh_check_task_op", AsyncMock(return_value="incomplete")),
        ):
            state = await _evaluate_wave_state(inner(), frozen, session, bot)

        self.assertEqual(state.status, "pending")
        bot.get_chat_member.assert_not_awaited()

    async def test_trust_kind_item_resolves_once_flyerhub_confirms_complete(self) -> None:
        session = fake_session()
        frozen = frozen_user([{
            "provider": "flyerhub", "url": "https://t.me/boostchan", "name": "Boost",
            "ref": "sig-1", "kind": "trust",
        }])
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", ""),
            patch.object(settings, "flyerhub_op_key", "op-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.flyerhub.fh_check_task_op", AsyncMock(return_value="complete")),
        ):
            state = await _evaluate_wave_state(inner(), frozen, session)

        self.assertEqual(state.status, "complete")


class AllProvidersFailTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_when_every_configured_provider_fails(self) -> None:
        session = fake_session()
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", "traffy-key"),
            patch.object(settings, "flyerhub_op_key", "op-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.traffy.get_traffy_tasks", AsyncMock(return_value=None)),
            patch("bot.services.flyerhub.fh_get_tasks_op", AsyncMock(return_value=None)),
        ):
            passed = await run_sponsor_wall_check(inner(), user(), session)

        self.assertFalse(passed)

    async def test_first_freeze_requires_every_configured_provider_to_answer(self) -> None:
        """Stricter than all_configured_integrations_failed: on the very
        first freeze, EVERY configured provider must succeed (even if
        another one already has enough sponsors) -- otherwise a temporary
        outage could freeze a wave missing that provider's mandatory
        sponsors and let the user pass them permanently."""
        session = fake_session()
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", "traffy-key"),
            patch.object(settings, "flyerhub_op_key", "op-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.traffy.get_traffy_tasks", AsyncMock(return_value=None)),
            patch("bot.services.flyerhub.fh_get_tasks_op", AsyncMock(return_value=offers("fh", 2))),
        ):
            passed = await run_sponsor_wall_check(inner(), user(), session)

        self.assertFalse(passed)

    async def test_recheck_of_an_already_frozen_wave_ignores_an_unrelated_provider_outage(self) -> None:
        """Once frozen, only providers that actually contributed to the
        SAVED wave are required to answer -- Traffy being configured but
        not part of THIS wave (no saved traffy items -> nothing to
        re-check -> a trivially successful empty result) must not block a
        recheck of a wave that never needed it."""
        session = fake_session()
        frozen = frozen_user(
            [{"provider": "flyerhub", "url": "https://t.me/fh0", "name": "FH", "ref": "sig-0"}]
        )
        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", "traffy-key"),
            patch.object(settings, "flyerhub_op_key", "op-key"),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.traffy.check_traffy_tasks", AsyncMock()) as check_traffy,
            patch("bot.services.flyerhub.fh_check_task_op", AsyncMock(return_value="complete")),
        ):
            passed = await run_sponsor_wall_check(inner(), frozen, session)

        self.assertTrue(passed)
        check_traffy.assert_not_awaited()


class BlockedSponsorWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_sponsor_url_is_loaded_from_the_repository_and_excluded(self) -> None:
        """End-to-end: _evaluate_wave_state must actually query
        BlockedSponsorRepository (not just accept a blocked_urls kwarg in
        isolation) and keep a matching sponsor out of the wave."""
        session = fake_session()
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", ""),
            patch.object(settings, "traffy_key", ""),
            patch.object(settings, "flyerhub_op_key", ""),
            patch("bot.database.repositories.settings.SettingsRepository.get_int", fake_get_int()),
            patch(
                "bot.database.repositories.blocked_sponsor.BlockedSponsorRepository.url_key_set",
                AsyncMock(return_value={"https://t.me/tg0"}),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 2))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
        ):
            state = await _evaluate_wave_state(inner(), user(), session)

        shown_urls = {item["url"] for item in state.items or []}
        self.assertNotIn("https://t.me/tg0", shown_urls)
        self.assertEqual(shown_urls, {"https://t.me/tg1"})


if __name__ == "__main__":
    unittest.main()
