import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import ChatGameRound, User
from bot.database.repositories.chat_game import ChatGameRoundRepository
from bot.handlers.group.games_doors import cb_doors_cashout, cb_doors_next, cb_doors_pick, msg_doors_start
from bot.handlers.group.games_maze import cb_maze_cashout, cb_maze_continue, msg_maze_start
from bot.handlers.group.games_roulette import msg_roulette_bet
from bot.handlers.group.games_safe import msg_safe_guess, msg_safe_start
from bot.services import chat_games


def _message(chat_id: int, user_id: int, text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Chat"),
        from_user=SimpleNamespace(id=user_id, first_name="U"),
        text=text,
        reply=AsyncMock(),
        answer=AsyncMock(),
    )


def _callback(chat_id: int, user_id: int, data: str):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Chat"),
        edit_text=AsyncMock(),
        answer=AsyncMock(),
    )
    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id, first_name="U"),
        data=data,
        answer=AsyncMock(),
    )


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _add_user(self, user_id: int, balance: str) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=user_id, first_name="U", stars_balance=Decimal(balance)))
            await session.commit()


class RouletteTests(ChatModelsTestCase):
    async def test_win_pays_out_and_lose_takes_the_bet(self) -> None:
        await self._add_user(1, "100")
        with patch("bot.handlers.group.games_roulette.roulette_spin", return_value="red"):
            message = _message(-1, 1, "ред 10")
            async with self.sessions() as session:
                await msg_roulette_bet(message, session)
        message.reply.assert_awaited_once()
        rendered = message.reply.await_args.args[0]
        self.assertIn("Угадал", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 1)
        self.assertEqual(user.stars_balance, Decimal("106.00"))  # 100 - 10 + 10*1.6

    async def test_lose_deducts_bet_only(self) -> None:
        await self._add_user(2, "100")
        with patch("bot.handlers.group.games_roulette.roulette_spin", return_value="black"):
            message = _message(-1, 2, "ред 10")
            async with self.sessions() as session:
                await msg_roulette_bet(message, session)
        async with self.sessions() as session:
            user = await session.get(User, 2)
        self.assertEqual(user.stars_balance, Decimal("90.00"))

    async def test_insufficient_balance_rejected_before_spin(self) -> None:
        await self._add_user(3, "1")
        message = _message(-1, 3, "ред 10")
        async with self.sessions() as session:
            await msg_roulette_bet(message, session)
        rendered = message.reply.await_args.args[0]
        self.assertIn("Недостаточно", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 3)
        self.assertEqual(user.stars_balance, Decimal("1"))


class SafeTests(ChatModelsTestCase):
    async def test_five_of_five_wins_immediately(self) -> None:
        await self._add_user(10, "100")
        with patch("bot.handlers.group.games_safe.generate_safe_code", return_value="13579"):
            message = _message(-2, 10, "сейф 5")
            async with self.sessions() as session:
                await msg_safe_start(message, session)

        guess_msg = _message(-2, 10, "13579")
        async with self.sessions() as session:
            await msg_safe_guess(guess_msg, session)
        rendered = guess_msg.reply.await_args.args[0]
        self.assertIn("5/5", rendered)

        async with self.sessions() as session:
            user = await session.get(User, 10)
            round_ = await ChatGameRoundRepository(session).get_active(-2, 10, "safe")
        self.assertEqual(user.stars_balance, Decimal("103.00"))  # 100 - 5 + 5*1.6
        self.assertIsNone(round_)

    async def test_exhausting_attempts_settles_on_best_guess(self) -> None:
        await self._add_user(11, "100")
        with patch("bot.handlers.group.games_safe.generate_safe_code", return_value="00000"):
            message = _message(-3, 11, "сейф 10")
            async with self.sessions() as session:
                await msg_safe_start(message, session)

        # First 5 guesses all wrong (0 matches), last guess gets exactly 3 right.
        for guess in ["11111", "22222", "33333", "44444", "55555"]:
            g = _message(-3, 11, guess)
            async with self.sessions() as session:
                await msg_safe_guess(g, session)
        final = _message(-3, 11, "00090")  # 4 of 5 digits match position (0,0,0,_,0)
        async with self.sessions() as session:
            await msg_safe_guess(final, session)
        rendered = final.reply.await_args.args[0]
        self.assertIn("4/5", rendered)

        async with self.sessions() as session:
            user = await session.get(User, 11)
        self.assertEqual(user.stars_balance, Decimal("102.00"))  # 100 - 10 + 10*1.2

    async def test_cannot_start_second_round_while_one_active(self) -> None:
        await self._add_user(12, "100")
        message = _message(-4, 12, "сейф 5")
        async with self.sessions() as session:
            await msg_safe_start(message, session)
        message2 = _message(-4, 12, "сейф 5")
        async with self.sessions() as session:
            await msg_safe_start(message2, session)
        rendered = message2.reply.await_args.args[0]
        self.assertIn("уже есть", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 12)
        self.assertEqual(user.stars_balance, Decimal("95"))  # only charged once

    async def test_random_five_digits_without_active_round_ignored_silently(self) -> None:
        message = _message(-5, 999, "42069")
        async with self.sessions() as session:
            await msg_safe_guess(message, session)
        message.reply.assert_not_awaited()


class MazeTests(ChatModelsTestCase):
    async def test_continue_on_empty_tile_advances_and_cashout_pays(self) -> None:
        await self._add_user(20, "100")
        with patch("bot.handlers.group.games_maze.maze_draw_tile", return_value="empty"):
            message = _message(-6, 20, "лабиринт 10")
            async with self.sessions() as session:
                await msg_maze_start(message, session)

            cb = _callback(-6, 20, "maze:continue")
            async with self.sessions() as session:
                await cb_maze_continue(cb, session)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Шаг: <b>1</b>", rendered)

        cashout_cb = _callback(-6, 20, "maze:cashout")
        async with self.sessions() as session:
            await cb_maze_cashout(cashout_cb, session)
        async with self.sessions() as session:
            user = await session.get(User, 20)
        # step=1, house_edge=0.24 default -> base = min(max_coeff, (1/0.82)*0.76) = 0.9268...
        expected_payout = round(10 * min(10.0, (1 / 0.82) * 0.76), 2)
        self.assertEqual(user.stars_balance, Decimal("90") + Decimal(str(expected_payout)))

    async def test_trap_without_shield_ends_run_and_takes_bet(self) -> None:
        await self._add_user(21, "100")
        with patch("bot.handlers.group.games_maze.maze_draw_tile", return_value="trap"):
            message = _message(-7, 21, "лабиринт 10")
            async with self.sessions() as session:
                await msg_maze_start(message, session)
            cb = _callback(-7, 21, "maze:continue")
            async with self.sessions() as session:
                await cb_maze_continue(cb, session)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Ловушка", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 21)
            round_ = await ChatGameRoundRepository(session).get_active(-7, 21, "maze")
        self.assertEqual(user.stars_balance, Decimal("90"))
        self.assertIsNone(round_)

    async def test_shield_absorbs_one_trap(self) -> None:
        await self._add_user(22, "100")
        message = _message(-8, 22, "лабиринт 10")
        async with self.sessions() as session:
            await msg_maze_start(message, session)

        with patch("bot.handlers.group.games_maze.maze_draw_tile", return_value="shield"):
            cb1 = _callback(-8, 22, "maze:continue")
            async with self.sessions() as session:
                await cb_maze_continue(cb1, session)

        with patch("bot.handlers.group.games_maze.maze_draw_tile", return_value="trap"):
            cb2 = _callback(-8, 22, "maze:continue")
            async with self.sessions() as session:
                await cb_maze_continue(cb2, session)
        rendered = cb2.message.edit_text.await_args.args[0]
        self.assertIn("щит поглотил удар", rendered.lower())
        async with self.sessions() as session:
            round_ = await ChatGameRoundRepository(session).get_active(-8, 22, "maze")
        self.assertIsNotNone(round_)  # still alive


class DoorsTests(ChatModelsTestCase):
    async def test_correct_door_advances_wrong_door_ends(self) -> None:
        await self._add_user(30, "100")
        with patch("bot.handlers.group.games_doors.doors_generate_safe_positions", return_value=[0, 1]):
            message = _message(-9, 30, "двери 10")
            async with self.sessions() as session:
                await msg_doors_start(message, session)

            cb = _callback(-9, 30, "doors:pick:0")
            async with self.sessions() as session:
                await cb_doors_pick(cb, session)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Приз", rendered)

        # Cash out at level 1 -> door_coeff_1 default 1.6
        cashout_cb = _callback(-9, 30, "doors:cashout")
        async with self.sessions() as session:
            await cb_doors_cashout(cashout_cb, session)
        async with self.sessions() as session:
            user = await session.get(User, 30)
        self.assertEqual(user.stars_balance, Decimal("90.00") + Decimal("16.00"))  # 10*1.6

    async def test_wrong_door_loses_bet(self) -> None:
        await self._add_user(31, "100")
        with patch("bot.handlers.group.games_doors.doors_generate_safe_positions", return_value=[0, 1]):
            message = _message(-10, 31, "двери 10")
            async with self.sessions() as session:
                await msg_doors_start(message, session)
            cb = _callback(-10, 31, "doors:pick:2")
            async with self.sessions() as session:
                await cb_doors_pick(cb, session)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Мина", rendered)
        async with self.sessions() as session:
            user = await session.get(User, 31)
            round_ = await ChatGameRoundRepository(session).get_active(-10, 31, "doors")
        self.assertEqual(user.stars_balance, Decimal("90"))
        self.assertIsNone(round_)

    async def test_next_regenerates_doors_for_new_level(self) -> None:
        await self._add_user(32, "100")
        with patch("bot.handlers.group.games_doors.doors_generate_safe_positions", return_value=[0, 1]):
            message = _message(-11, 32, "двери 10")
            async with self.sessions() as session:
                await msg_doors_start(message, session)
            cb = _callback(-11, 32, "doors:pick:0")
            async with self.sessions() as session:
                await cb_doors_pick(cb, session)

        next_cb = _callback(-11, 32, "doors:next")
        async with self.sessions() as session:
            await cb_doors_next(next_cb, session)
        rendered = next_cb.message.edit_text.await_args.args[0]
        self.assertIn("Уровень: <b>2/10</b>", rendered)


if __name__ == "__main__":
    unittest.main()
