import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import User, VirusInfection
from bot.database.repositories.virus import VirusInfectionRepository
from bot.handlers.group.games_virus import (
    cb_virus_buy_ammo,
    cb_virus_cure_herbs,
    cb_virus_cure_medicine,
    msg_virus_attack,
    msg_virus_cure,
)


def _message(user_id: int, text: str, reply_user_id: int | None = None, reply_username: str | None = "target"):
    reply_to = None
    if reply_user_id is not None:
        reply_to = SimpleNamespace(from_user=SimpleNamespace(id=reply_user_id, username=reply_username, first_name="Target"))
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id, username="attacker", first_name="Attacker"),
        reply_to_message=reply_to,
        reply=AsyncMock(),
    )


def _callback(user_id: int, data: str):
    message = SimpleNamespace(answer=AsyncMock())
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, username="attacker", first_name="Attacker"),
        message=message,
        answer=AsyncMock(),
    )


class VirusGameTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _add_user(self, user_id: int, balance: str = "100", **overrides) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=user_id, first_name="U", stars_balance=Decimal(balance), **overrides))
            await session.commit()

    async def _balance(self, user_id: int) -> Decimal:
        async with self.sessions() as session:
            user = await session.get(User, user_id)
            return user.stars_balance

    async def _infection(self, user_id: int) -> VirusInfection | None:
        async with self.sessions() as session:
            return await VirusInfectionRepository(session).get(user_id)


class AttackValidationTests(VirusGameTestCase):
    async def test_self_target_rejected(self) -> None:
        await self._add_user(1)
        message = _message(1, "Вирус 10", reply_user_id=1)
        async with self.sessions() as session:
            await msg_virus_attack(message, session)
        self.assertIn("самого себя", message.reply.await_args.args[0])
        self.assertEqual(await self._balance(1), Decimal("100"))

    async def test_target_not_registered_rejected(self) -> None:
        await self._add_user(1)
        message = _message(1, "Вирус 10", reply_user_id=999)
        async with self.sessions() as session:
            await msg_virus_attack(message, session)
        self.assertIn("не зарегистрирована", message.reply.await_args.args[0])
        self.assertEqual(await self._balance(1), Decimal("100"))

    async def test_target_already_infected_rejected(self) -> None:
        await self._add_user(1)
        await self._add_user(2)
        await self._add_user(3)
        async with self.sessions() as session:
            await VirusInfectionRepository(session).create(2, 3, "light")
        message = _message(1, "Вирус 10", reply_user_id=2)
        async with self.sessions() as session:
            await msg_virus_attack(message, session)
        self.assertIn("уже заражён", message.reply.await_args.args[0])
        self.assertEqual(await self._balance(1), Decimal("100"))

    async def test_insufficient_balance_rejected_and_cooldown_not_set(self) -> None:
        await self._add_user(1, balance="1")
        await self._add_user(2)
        message = _message(1, "Вирус 10", reply_user_id=2)
        async with self.sessions() as session:
            await msg_virus_attack(message, session)
        self.assertIn("Недостаточно", message.reply.await_args.args[0])
        async with self.sessions() as session:
            attacker = await session.get(User, 1)
        self.assertIsNone(attacker.virus_last_used_at)

    async def test_attacker_not_registered_shows_registration_prompt(self) -> None:
        await self._add_user(2)
        message = _message(1, "Вирус 10", reply_user_id=2)
        async with self.sessions() as session:
            await msg_virus_attack(message, session)
        args, kwargs = message.reply.await_args
        self.assertIn("регистрацию", args[0])
        self.assertIsNotNone(kwargs.get("reply_markup"))


class AttackOutcomeTests(VirusGameTestCase):
    async def test_successful_attack_credits_payout_and_creates_infection(self) -> None:
        await self._add_user(1, balance="100")
        await self._add_user(2, balance="0")
        message = _message(1, "Вирус 10", reply_user_id=2)
        with (
            patch("bot.handlers.group.games_virus.roll_infect_success", return_value=True),
            patch("bot.handlers.group.games_virus.roll_virus_type", return_value="normal"),
        ):
            async with self.sessions() as session:
                await msg_virus_attack(message, session)

        self.assertIn("заражён", message.reply.await_args.args[0])
        # stake (10) debited, payout (10 * 1.5 = 15) credited: 100 - 10 + 15 = 105
        self.assertEqual(await self._balance(1), Decimal("105.00"))
        infection = await self._infection(2)
        self.assertIsNotNone(infection)
        self.assertEqual(infection.virus_type, "normal")
        self.assertEqual(infection.infector_user_id, 1)

    async def test_failed_attack_charges_stake_and_offers_ammo(self) -> None:
        await self._add_user(1, balance="100")
        await self._add_user(2)
        message = _message(1, "Вирус 10", reply_user_id=2)
        with patch("bot.handlers.group.games_virus.roll_infect_success", return_value=False):
            async with self.sessions() as session:
                await msg_virus_attack(message, session)

        args, kwargs = message.reply.await_args
        self.assertIn("Не удалось заразить", args[0])
        self.assertIsNotNone(kwargs.get("reply_markup"))
        self.assertEqual(await self._balance(1), Decimal("90"))
        self.assertIsNone(await self._infection(2))

    async def test_cooldown_blocks_second_attack(self) -> None:
        await self._add_user(1, balance="100")
        await self._add_user(2)
        await self._add_user(3)
        message1 = _message(1, "Вирус 10", reply_user_id=2)
        with patch("bot.handlers.group.games_virus.roll_infect_success", return_value=False):
            async with self.sessions() as session:
                await msg_virus_attack(message1, session)

        message2 = _message(1, "Вирус 10", reply_user_id=3)
        async with self.sessions() as session:
            await msg_virus_attack(message2, session)
        self.assertIn("24 часа", message2.reply.await_args.args[0])
        # Only the first stake was charged.
        self.assertEqual(await self._balance(1), Decimal("90"))

    async def test_expired_cooldown_allows_attack_again(self) -> None:
        await self._add_user(1, balance="100", virus_last_used_at=datetime.utcnow() - timedelta(hours=25))
        await self._add_user(2)
        message = _message(1, "Вирус 10", reply_user_id=2)
        with patch("bot.handlers.group.games_virus.roll_infect_success", return_value=False):
            async with self.sessions() as session:
                await msg_virus_attack(message, session)
        self.assertIn("Не удалось заразить", message.reply.await_args.args[0])
        self.assertEqual(await self._balance(1), Decimal("90"))


class AmmoTests(VirusGameTestCase):
    async def test_ammo_success_grants_immediate_retry_and_charges_stake_again(self) -> None:
        await self._add_user(1, balance="100")
        await self._add_user(2)
        callback = _callback(1, "virus:ammo:2:10")
        with (
            patch("bot.handlers.group.games_virus.roll_ammo_success", return_value=True),
            patch("bot.handlers.group.games_virus.roll_infect_success", return_value=True),
            patch("bot.handlers.group.games_virus.roll_virus_type", return_value="light"),
        ):
            async with self.sessions() as session:
                await cb_virus_buy_ammo(callback, session)

        text = callback.message.answer.await_args.args[0]
        self.assertIn("Боеприпас сработал", text)
        self.assertIn("заражён", text)
        # 100 - 5 (ammo) - 10 (retry stake) + 12 (10 * 1.2 payout) = 97
        self.assertEqual(await self._balance(1), Decimal("97.00"))
        async with self.sessions() as session:
            attacker = await session.get(User, 1)
        self.assertFalse(attacker.virus_bonus_attempt)

    async def test_ammo_failure_burns_rp_without_granting_bonus(self) -> None:
        await self._add_user(1, balance="100")
        await self._add_user(2)
        callback = _callback(1, "virus:ammo:2:10")
        with patch("bot.handlers.group.games_virus.roll_ammo_success", return_value=False):
            async with self.sessions() as session:
                await cb_virus_buy_ammo(callback, session)

        self.assertIn("бракованным", callback.message.answer.await_args.args[0])
        self.assertEqual(await self._balance(1), Decimal("95"))
        async with self.sessions() as session:
            attacker = await session.get(User, 1)
        self.assertFalse(attacker.virus_bonus_attempt)

    async def test_ammo_insufficient_balance_rejected(self) -> None:
        await self._add_user(1, balance="1")
        await self._add_user(2)
        callback = _callback(1, "virus:ammo:2:10")
        async with self.sessions() as session:
            await cb_virus_buy_ammo(callback, session)
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))
        self.assertEqual(await self._balance(1), Decimal("1"))


class CureTests(VirusGameTestCase):
    async def test_not_infected_rejected(self) -> None:
        await self._add_user(1)
        message = _message(1, "Антивирус")
        async with self.sessions() as session:
            await msg_virus_cure(message, session)
        self.assertIn("не заражены", message.reply.await_args.args[0])

    async def test_medicine_always_cures(self) -> None:
        await self._add_user(1, balance="100")
        await self._add_user(2)
        async with self.sessions() as session:
            await VirusInfectionRepository(session).create(1, 2, "light")
        callback = _callback(1, "virus:cure:medicine")
        async with self.sessions() as session:
            await cb_virus_cure_medicine(callback, session)
        self.assertIn("вылечились", callback.message.answer.await_args.args[0])
        self.assertIsNone(await self._infection(1))
        self.assertEqual(await self._balance(1), Decimal("90"))

    async def test_herbs_success_cures(self) -> None:
        await self._add_user(1, balance="100")
        await self._add_user(2)
        async with self.sessions() as session:
            await VirusInfectionRepository(session).create(1, 2, "light")
        callback = _callback(1, "virus:cure:herbs")
        with patch("bot.handlers.group.games_virus.roll_herbs_success", return_value=True):
            async with self.sessions() as session:
                await cb_virus_cure_herbs(callback, session)
        self.assertIn("вылечились", callback.message.answer.await_args.args[0])
        self.assertIsNone(await self._infection(1))

    async def test_herbs_failure_keeps_infection_but_still_charges(self) -> None:
        await self._add_user(1, balance="100")
        await self._add_user(2)
        async with self.sessions() as session:
            await VirusInfectionRepository(session).create(1, 2, "light")
        callback = _callback(1, "virus:cure:herbs")
        with patch("bot.handlers.group.games_virus.roll_herbs_success", return_value=False):
            async with self.sessions() as session:
                await cb_virus_cure_herbs(callback, session)
        self.assertIn("не помогли", callback.message.answer.await_args.args[0])
        self.assertIsNotNone(await self._infection(1))
        self.assertEqual(await self._balance(1), Decimal("99"))


if __name__ == "__main__":
    unittest.main()
