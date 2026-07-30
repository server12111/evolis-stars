import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message
from aiogram.types import User as TelegramUser

from bot.handlers.start import cmd_start
from bot.handlers.start import settings as start_settings
from bot.middlewares.sponsor_wall import SponsorWallMiddleware, settings


def message(text: str = "Бонус") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=100, type="private"),
        from_user=TelegramUser(id=100, is_bot=False, first_name="Test"),
        text=text,
    )


class SponsorWallSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_without_providers_still_routes_to_captcha(self) -> None:
        incoming = message("/start")
        db_user = SimpleNamespace(
            user_id=100,
            is_admin=False,
            sponsors_verified=False,
            phone_verified=True,
        )
        session = SimpleNamespace(commit=AsyncMock())

        def close_background(coroutine, **_kwargs):
            coroutine.close()

        with (
            patch.object(start_settings, "tgrass_code", ""),
            patch.object(start_settings, "botohub_key", ""),
            patch(
                "bot.handlers.start.spawn_background",
                side_effect=close_background,
            ),
            patch(
                "bot.handlers.start._show_captcha",
                AsyncMock(),
            ) as show_captcha,
            patch(
                "bot.handlers.start._send_main_menu",
                AsyncMock(),
            ) as main_menu,
        ):
            await cmd_start(
                incoming,
                session,
                db_user,
                False,
                SimpleNamespace(),
                SimpleNamespace(),
            )

        show_captcha.assert_awaited_once()
        main_menu.assert_not_awaited()
        self.assertFalse(db_user.sponsors_verified)

    async def test_incomplete_wave_never_reaches_feature_handler(self) -> None:
        saved = [
            {
                "provider": "tgrass",
                "url": "https://t.me/required",
                "name": "Required",
            }
        ]
        db_user = SimpleNamespace(
            user_id=100,
            is_admin=False,
            sponsors_verified=False,
            phone_verified=False,
            sponsor_wave=1,
            sponsor_wave_one=json.dumps(saved),
            sponsor_wave_two=None,
        )
        session = SimpleNamespace(commit=AsyncMock())
        handler = AsyncMock()

        with (
            patch.object(settings, "tgrass_code", "configured"),
            patch.object(settings, "botohub_key", ""),
            patch(
                "bot.services.tgrass.check_tgrass",
                AsyncMock(
                    return_value=[
                        {
                            "url": "https://t.me/required",
                            "name": "Required",
                        }
                    ]
                ),
            ),
            patch(
                "bot.services.botohub.check_botohub",
                AsyncMock(return_value=[]),
            ),
            patch(
                "bot.middlewares.sponsor_wall._show_wave",
                AsyncMock(),
            ) as show_wave,
            patch(
                "bot.middlewares.sponsor_wall.SettingsRepository.get_int",
                AsyncMock(return_value=6),
            ),
        ):
            await SponsorWallMiddleware()(
                handler,
                message(),
                {
                    "db_user": db_user,
                    "session": session,
                    "state": object(),
                    "bot": None,
                },
            )

        handler.assert_not_awaited()
        show_wave.assert_awaited_once()

    async def test_completed_waves_require_phone_before_features(self) -> None:
        db_user = SimpleNamespace(
            user_id=100,
            is_admin=False,
            sponsors_verified=False,
            phone_verified=False,
            sponsor_wave=3,
            sponsor_wave_one="[]",
            sponsor_wave_two="[]",
        )
        session = SimpleNamespace(commit=AsyncMock())
        handler = AsyncMock()

        with (
            patch.object(settings, "tgrass_code", "configured"),
            patch.object(settings, "botohub_key", ""),
            patch(
                "bot.services.tgrass.check_tgrass",
                AsyncMock(return_value=[]),
            ),
            patch(
                "bot.services.botohub.check_botohub",
                AsyncMock(return_value=[]),
            ),
            patch(
                "bot.middlewares.sponsor_wall._prompt_phone",
                AsyncMock(),
            ) as prompt,
            patch(
                "bot.middlewares.sponsor_wall.SettingsRepository.get_int",
                AsyncMock(return_value=6),
            ),
        ):
            await SponsorWallMiddleware()(
                handler,
                message(),
                {
                    "db_user": db_user,
                    "session": session,
                    "state": object(),
                    "bot": None,
                },
            )

        handler.assert_not_awaited()
        prompt.assert_awaited_once()

    async def test_no_providers_still_require_captcha_after_phone(self) -> None:
        db_user = SimpleNamespace(
            user_id=100,
            is_admin=False,
            sponsors_verified=False,
            phone_verified=True,
            sponsor_wave=0,
            sponsor_wave_one=None,
            sponsor_wave_two=None,
        )
        session = SimpleNamespace(commit=AsyncMock())
        handler = AsyncMock()

        with (
            patch.object(settings, "tgrass_code", ""),
            patch.object(settings, "botohub_key", ""),
            patch(
                "bot.handlers.start._show_captcha",
                AsyncMock(),
            ) as show_captcha,
        ):
            await SponsorWallMiddleware()(
                handler,
                message(),
                {
                    "db_user": db_user,
                    "session": session,
                    "state": object(),
                    "bot": None,
                },
            )

        handler.assert_not_awaited()
        show_captcha.assert_awaited_once()
        self.assertFalse(db_user.sponsors_verified)


if __name__ == "__main__":
    unittest.main()
