import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.middlewares.sponsor_wall import run_sponsor_wall_check, settings


def offers(prefix: str, count: int) -> list[dict]:
    return [
        {"name": f"Channel {i}", "url": f"https://t.me/{prefix}{i}"}
        for i in range(count)
    ]


def inner() -> SimpleNamespace:
    return SimpleNamespace(from_user=None, message=None, answer=AsyncMock())


def user(**kwargs) -> SimpleNamespace:
    base = dict(
        user_id=1,
        sponsor_wave=0,
        sponsor_wave_one=None,
        sponsor_wave_two=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def fake_get_int(min_sponsors: int, wave_size: int = 10):
    async def _get_int(self, key: str, default: int = 0) -> int:
        return {
            "min_sponsors_for_reward": min_sponsors,
            "sponsor_max_channels": wave_size,
        }.get(key, default)

    return _get_int


class PiarFlowTopUpTests(unittest.IsolatedAsyncioTestCase):
    async def test_piarflow_skipped_when_free_providers_already_cover_minimum(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", "cfg"),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=3),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 3))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.piarflow.get_sponsors", AsyncMock()) as get_sponsors,
            patch("bot.services.piarflow.check_sponsors", AsyncMock()) as check_sponsors,
        ):
            await run_sponsor_wall_check(inner(), user(), session)

        get_sponsors.assert_not_awaited()
        check_sponsors.assert_not_awaited()

    async def test_piarflow_tops_up_exactly_the_gap(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", "cfg"),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=6),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=offers("tg", 2))),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch(
                "bot.services.piarflow.get_sponsors",
                AsyncMock(return_value=offers("pf", 4)),
            ) as get_sponsors,
        ):
            await run_sponsor_wall_check(inner(), user(), session)

        get_sponsors.assert_awaited_once()
        self.assertEqual(get_sponsors.await_args.kwargs.get("max_sponsors"), 4)

    async def test_frozen_wave_rechecks_piarflow_only_if_it_supplied_a_sponsor(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        frozen_with_piarflow = user(
            sponsor_wave=1,
            sponsor_wave_one=json.dumps(
                [{"provider": "piarflow", "url": "https://t.me/pf0", "name": "PF"}]
            ),
        )
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", "cfg"),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=6),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.piarflow.check_sponsors", AsyncMock()) as check_sponsors,
            patch(
                "bot.services.piarflow.get_sponsors",
                AsyncMock(return_value=[]),
            ) as get_sponsors,
        ):
            await run_sponsor_wall_check(inner(), frozen_with_piarflow, session)

        check_sponsors.assert_awaited_once_with("cfg", 1, ["https://t.me/pf0"])
        get_sponsors.assert_awaited_once()
        self.assertEqual(get_sponsors.await_args.kwargs.get("max_sponsors"), 20)

    async def test_frozen_wave_without_piarflow_sponsor_skips_piarflow_entirely(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        frozen_tg_only = user(
            sponsor_wave=1,
            sponsor_wave_one=json.dumps(
                [{"provider": "tgrass", "url": "https://t.me/tg0", "name": "TG"}]
            ),
        )
        with (
            patch.object(settings, "tgrass_code", "cfg"),
            patch.object(settings, "botohub_key", "cfg"),
            patch.object(settings, "piarflow_key", "cfg"),
            patch(
                "bot.database.repositories.settings.SettingsRepository.get_int",
                fake_get_int(min_sponsors=6),
            ),
            patch("bot.services.tgrass.check_tgrass", AsyncMock(return_value=[])),
            patch("bot.services.botohub.check_botohub", AsyncMock(return_value=[])),
            patch("bot.services.piarflow.check_sponsors", AsyncMock()) as check_sponsors,
            patch("bot.services.piarflow.get_sponsors", AsyncMock()) as get_sponsors,
        ):
            await run_sponsor_wall_check(inner(), frozen_tg_only, session)

        check_sponsors.assert_not_awaited()
        get_sponsors.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
