import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.handlers.withdraw import _rp_cost, cb_withdraw_amount


def _callback(data: str):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, data=data, answer=AsyncMock())


class RpCostFormulaTests(unittest.TestCase):
    def test_spec_examples_exactly(self) -> None:
        # 15 -> 45, 25 -> 75, 50 -> 150, 100 -> 300 RP⭐️
        self.assertEqual(_rp_cost(15), Decimal("45"))
        self.assertEqual(_rp_cost(25), Decimal("75"))
        self.assertEqual(_rp_cost(50), Decimal("150"))
        self.assertEqual(_rp_cost(100), Decimal("300"))


class WithdrawAmountGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_only_the_four_fixed_amounts_are_accepted(self) -> None:
        db_user = SimpleNamespace(stars_balance=Decimal("10000"))
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
        for bad in (10, 20, 30, 75, 200, 1):
            cb = _callback(f"withdraw:amount:{bad}")
            async with self.sessions() as session:
                with patch("bot.handlers.withdraw.SettingsRepository.get_bool", AsyncMock(return_value=True)):
                    await cb_withdraw_amount(cb, db_user, state, session)
            cb.answer.assert_awaited_with("❌ Неверная сумма.", show_alert=True)

    async def test_insufficient_rp_shows_clear_message_not_bypassed(self) -> None:
        db_user = SimpleNamespace(stars_balance=Decimal("10"))  # needs 45 for the 15-star tier
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
        cb = _callback("withdraw:amount:15")
        async with self.sessions() as session:
            with patch("bot.handlers.withdraw.SettingsRepository.get_bool", AsyncMock(return_value=True)):
                await cb_withdraw_amount(cb, db_user, state, session)
        rendered = cb.answer.await_args.args[0]
        self.assertIn("RP⭐️", rendered)
        state.set_state.assert_not_awaited()

    async def test_exact_balance_for_100_tier_succeeds(self) -> None:
        db_user = SimpleNamespace(stars_balance=Decimal("300"))  # exactly enough for 100 -> 300 RP
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
        cb = _callback("withdraw:amount:100")
        async with self.sessions() as session:
            with patch("bot.handlers.withdraw.SettingsRepository.get_bool", AsyncMock(return_value=True)):
                await cb_withdraw_amount(cb, db_user, state, session)
        state.set_state.assert_awaited_once()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("300 RP⭐️", rendered)
        self.assertIn("100 Telegram ⭐", rendered)


if __name__ == "__main__":
    unittest.main()
