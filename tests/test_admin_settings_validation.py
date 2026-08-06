import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.admin.games import msg_game_value
from bot.handlers.admin.settings import msg_setting_value


def _admin() -> SimpleNamespace:
    return SimpleNamespace(is_admin=True, user_id=1)


def _state(data: dict) -> SimpleNamespace:
    return SimpleNamespace(get_data=AsyncMock(return_value=dict(data)), clear=AsyncMock())


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, answer=AsyncMock())


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class MinesHouseEdgeValidationTests(ChatModelsTestCase):
    async def test_house_edge_of_exactly_one_is_rejected(self) -> None:
        message = _message("1")
        state = _state({"setting_key": "mines_house_edge"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("mines_house_edge", "unset")

        self.assertIn("Комиссия", message.answer.await_args.args[0])
        self.assertEqual(saved, "0.20")  # untouched default, "1" was never written
        state.clear.assert_not_awaited()

    async def test_house_edge_below_one_is_accepted(self) -> None:
        message = _message("0.3")
        state = _state({"setting_key": "mines_house_edge"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("mines_house_edge", "unset")
        self.assertEqual(saved, "0.3")

    async def test_max_coeff_of_zero_is_rejected(self) -> None:
        message = _message("0")
        state = _state({"setting_key": "mines_max_coeff"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("mines_max_coeff", "unset")
        self.assertIn("больше 0", message.answer.await_args.args[0])
        self.assertEqual(saved, "10")  # untouched default, "0" was never written

    async def test_max_coeff_positive_is_accepted(self) -> None:
        message = _message("15")
        state = _state({"setting_key": "mines_max_coeff"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("mines_max_coeff", "unset")
        self.assertEqual(saved, "15.0")


class BonusMinMaxOrderingValidationTests(ChatModelsTestCase):
    async def test_setting_min_above_current_max_is_rejected(self) -> None:
        message = _message("5")
        state = _state({"setting_key": "bonus_min"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("bonus_min", "unset")
        self.assertIn("не может быть больше макс", message.answer.await_args.args[0])
        self.assertEqual(saved, "0.1")  # default never overwritten by the rejected "5"

    async def test_setting_max_below_current_min_is_rejected(self) -> None:
        message = _message("0.01")
        state = _state({"setting_key": "bonus_max"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("bonus_max", "unset")
        self.assertIn("не может быть меньше мин", message.answer.await_args.args[0])
        self.assertEqual(saved, "1.0")  # default never overwritten by the rejected "0.01"

    async def test_valid_min_max_pair_is_accepted(self) -> None:
        message_min = _message("0.2")
        state_min = _state({"setting_key": "bonus_min"})
        message_max = _message("2.0")
        state_max = _state({"setting_key": "bonus_max"})
        async with self.sessions() as session:
            await msg_setting_value(message_min, state_min, session, _admin())
            await msg_setting_value(message_max, state_max, session, _admin())
            saved_min = await SettingsRepository(session).get("bonus_min", "unset")
            saved_max = await SettingsRepository(session).get("bonus_max", "unset")
        self.assertEqual(saved_min, "0.2")
        self.assertEqual(saved_max, "2.0")


class GameCoeffValidationTests(ChatModelsTestCase):
    async def test_coefficient_above_100_is_rejected_as_likely_typo(self) -> None:
        message = _message("200")
        state = _state({"game_type": "football", "param": "coeff_goal"})
        async with self.sessions() as session:
            await msg_game_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("game_football_coeff_goal", "unset")
        self.assertIn("опечатка", message.answer.await_args.args[0])
        self.assertEqual(saved, "2.0")  # untouched default, "200" was never written

    async def test_normal_coefficient_is_accepted(self) -> None:
        message = _message("2.0")
        state = _state({"game_type": "football", "param": "coeff_goal"})
        async with self.sessions() as session:
            await msg_game_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("game_football_coeff_goal", "unset")
        self.assertEqual(saved, "2.0")

    async def test_min_bet_is_not_subject_to_the_100_ceiling(self) -> None:
        message = _message("150")
        state = _state({"game_type": "football", "param": "min_bet"})
        async with self.sessions() as session:
            await msg_game_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("game_football_min_bet", "unset")
        self.assertEqual(saved, "150.0")


class VcSettingsValidationTests(ChatModelsTestCase):
    async def test_rate_tier_of_zero_is_rejected(self) -> None:
        message = _message("0")
        state = _state({"setting_key": "vc_rate_tier1"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("vc_rate_tier1", "unset")
        self.assertIn("больше 0", message.answer.await_args.args[0])
        self.assertEqual(saved, "1000")  # untouched default

    async def test_rate_tier_positive_is_accepted(self) -> None:
        message = _message("1100")
        state = _state({"setting_key": "vc_rate_tier1"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("vc_rate_tier1", "unset")
        self.assertEqual(saved, "1100.0")

    async def test_min_withdrawal_above_max_is_rejected(self) -> None:
        message = _message("600000")
        state = _state({"setting_key": "vc_min_withdrawal"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("vc_min_withdrawal", "unset")
        self.assertIn("не может быть больше макс", message.answer.await_args.args[0])
        self.assertEqual(saved, "10000")  # untouched default

    async def test_min_withdrawal_requires_an_integer(self) -> None:
        message = _message("10000.5")
        state = _state({"setting_key": "vc_min_withdrawal"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("vc_min_withdrawal", "unset")
        self.assertIn("целое число", message.answer.await_args.args[0])
        self.assertEqual(saved, "10000")

    async def test_max_withdrawal_below_min_is_rejected(self) -> None:
        message = _message("5000")
        state = _state({"setting_key": "vc_max_withdrawal"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("vc_max_withdrawal", "unset")
        self.assertIn("не может быть меньше мин", message.answer.await_args.args[0])
        self.assertEqual(saved, "500000")

    async def test_mandatory_channel_text_setting_is_saved_verbatim(self) -> None:
        message = _message("https://t.me/SomeOtherChat")
        state = _state({"setting_key": "vc_mandatory_channel"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("vc_mandatory_channel", "unset")
        self.assertEqual(saved, "https://t.me/SomeOtherChat")
        state.clear.assert_awaited_once()

    async def test_mandatory_channel_rejects_non_url_text(self) -> None:
        # Saved verbatim into an InlineKeyboardButton(url=...) later --
        # a bare @username would make Telegram reject the button at send
        # time, silently breaking the subscribe-gate prompt.
        message = _message("@SomeOtherChat")
        state = _state({"setting_key": "vc_mandatory_channel"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("vc_mandatory_channel", "unset")
        self.assertIn("полная ссылка", message.answer.await_args.args[0])
        self.assertEqual(saved, "https://t.me/VirusikChat")  # untouched default
        state.clear.assert_not_awaited()

    async def test_mandatory_channel_rejects_empty_text(self) -> None:
        message = _message("   ")
        state = _state({"setting_key": "vc_mandatory_channel"})
        async with self.sessions() as session:
            await msg_setting_value(message, state, session, _admin())
            saved = await SettingsRepository(session).get("vc_mandatory_channel", "unset")
        self.assertIn("непустое", message.answer.await_args.args[0])
        self.assertEqual(saved, "https://t.me/VirusikChat")  # untouched default
        state.clear.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
