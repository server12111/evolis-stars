import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, ChatMembership, GameSession, User
from bot.handlers.admin.stats import cb_admin_stats_bot, cb_admin_stats_chats
from bot.handlers.group.info import msg_group_profile, msg_info, msg_roulette_log, msg_top_users


def _message(text: str, user_id: int = 1, chat_id: int = -1):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id, title="Chat"),
        from_user=SimpleNamespace(id=user_id, first_name="U"),
        reply=AsyncMock(),
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


class InfoCommandTests(ChatModelsTestCase):
    async def test_info_aliases_show_help_with_ad_line(self) -> None:
        for alias in ["команды", "инфо", "Команды", "ИНФО"]:
            message = _message(alias)
            await msg_info(message)
            message.answer.assert_awaited_once()
            rendered = message.answer.await_args.args[0]
            self.assertIn("@EvolisStarsBot", rendered)
            self.assertIn("башня", rendered)

    async def test_top_shows_users_ordered_by_balance(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="A", stars_balance=Decimal("50")))
            session.add(User(user_id=2, first_name="B", stars_balance=Decimal("200")))
            session.add(ChatMembership(chat_id=-1, user_id=1))
            session.add(ChatMembership(chat_id=-1, user_id=2))
            await session.commit()

        message = _message("топ")
        async with self.sessions() as session:
            await msg_top_users(message, session)
        rendered = message.answer.await_args.args[0]
        self.assertLess(rendered.index("B"), rendered.index("A"))

    async def test_top_is_scoped_to_the_current_chat(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="A", stars_balance=Decimal("50")))
            session.add(User(user_id=2, first_name="B", stars_balance=Decimal("200")))
            session.add(ChatMembership(chat_id=-1, user_id=1))
            session.add(ChatMembership(chat_id=-2, user_id=2))  # different chat
            await session.commit()

        message = _message("топ", chat_id=-1)
        async with self.sessions() as session:
            await msg_top_users(message, session)
        rendered = message.answer.await_args.args[0]
        self.assertIn("A", rendered)
        self.assertNotIn("B", rendered)

    async def test_log_shows_recent_roulette_games_only(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=3, first_name="C", stars_balance=Decimal("10")))
            session.add(GameSession(
                user_id=3, game_type="roulette", bet=Decimal("10"),
                result="win", payout=Decimal("22"), chat_id=-1,
                bet_choice="red", result_choice="red",
            ))
            session.add(GameSession(
                user_id=3, game_type="tower", bet=Decimal("5"),
                result="lose", payout=Decimal("0"), chat_id=-1,
            ))
            await session.commit()

        message = _message("лог")
        async with self.sessions() as session:
            await msg_roulette_log(message, session)
        rendered = message.answer.await_args.args[0]
        self.assertIn("10⭐ — 🔴", rendered)
        self.assertNotIn("tower", rendered.lower())

    async def test_log_is_scoped_to_the_current_chat(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=3, first_name="C", stars_balance=Decimal("10")))
            session.add(GameSession(
                user_id=3, game_type="roulette", bet=Decimal("10"),
                result="win", payout=Decimal("22"), chat_id=-1,
                bet_choice="red", result_choice="red",
            ))
            session.add(GameSession(
                user_id=3, game_type="roulette", bet=Decimal("7"),
                result="lose", payout=Decimal("0"), chat_id=-2,
                bet_choice="black", result_choice="black",
            ))
            await session.commit()

        message = _message("лог", chat_id=-1)
        async with self.sessions() as session:
            await msg_roulette_log(message, session)
        rendered = message.answer.await_args.args[0]
        self.assertIn("10⭐ — 🔴", rendered)
        self.assertNotIn("7⭐ — ⚫️", rendered)

    async def test_log_with_no_games_replies_gracefully(self) -> None:
        message = _message("лог", user_id=999)
        async with self.sessions() as session:
            await msg_roulette_log(message, session)
        message.reply.assert_awaited_once()

    async def test_log_shows_username_in_monospace_and_win_result(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=8, first_name="Nick", username="realuser", stars_balance=Decimal("0")))
            session.add(GameSession(
                user_id=8, game_type="roulette", bet=Decimal("10"),
                result="win", payout=Decimal("22"), chat_id=-1,
                bet_choice="red", result_choice="red",
            ))
            await session.commit()

        message = _message("лог", chat_id=-1)
        async with self.sessions() as session:
            await msg_roulette_log(message, session)
        rendered = message.answer.await_args.args[0]
        self.assertIn("<code>@realuser</code>", rendered)
        self.assertIn("✅", rendered)
        self.assertIn("22.00", rendered)  # payout shown on a win

    async def test_log_falls_back_to_first_name_and_shows_loss_result(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=9, first_name="NoHandle", stars_balance=Decimal("0")))
            session.add(GameSession(
                user_id=9, game_type="roulette", bet=Decimal("15"),
                result="lose", payout=Decimal("0"), chat_id=-1,
                bet_choice="black", result_choice="white",
            ))
            await session.commit()

        message = _message("лог", chat_id=-1)
        async with self.sessions() as session:
            await msg_roulette_log(message, session)
        rendered = message.answer.await_args.args[0]
        self.assertIn("<code>NoHandle</code>", rendered)
        self.assertIn("❌", rendered)
        self.assertIn("-15", rendered)

    async def test_profile_aliases_show_balance_and_game_counts(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=5, first_name="P", username="pname", stars_balance=Decimal("42.50")))
            session.add(GameSession(
                user_id=5, game_type="roulette", bet=Decimal("1"),
                result="win", payout=Decimal("1.6"), chat_id=-7,
            ))
            session.add(GameSession(
                user_id=5, game_type="tower", bet=Decimal("1"),
                result="lose", payout=Decimal("0"), chat_id=-7,
            ))
            session.add(GameSession(
                user_id=5, game_type="roulette", bet=Decimal("1"),
                result="lose", payout=Decimal("0"), chat_id=-9999,  # a different chat
            ))
            await session.commit()

        for alias in ["профиль", "пас", "Профиль", "ПАС"]:
            message = _message(alias, user_id=5, chat_id=-7)
            async with self.sessions() as session:
                await msg_group_profile(message, session)
            message.reply.assert_awaited_once()
            rendered = message.reply.await_args.args[0]
            self.assertIn("@pname", rendered)
            self.assertIn("42.50", rendered)
            self.assertIn("Игр сыграно всего: <b>3</b>", rendered)
            self.assertIn("Игр в этом чате: <b>2</b>", rendered)
            self.assertNotIn("VIP", rendered)

    async def test_profile_shows_vip_line_only_for_vip_users(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=7, first_name="V", stars_balance=Decimal("1"), is_vip=True))
            await session.commit()

        message = _message("профиль", user_id=7, chat_id=-7)
        async with self.sessions() as session:
            await msg_group_profile(message, session)
        rendered = message.reply.await_args.args[0]
        self.assertIn("VIP", rendered)

    async def test_profile_unregistered_user_gets_registration_prompt(self) -> None:
        message = _message("профиль", user_id=6, chat_id=-7)
        async with self.sessions() as session:
            await msg_group_profile(message, session)
        args, kwargs = message.reply.await_args
        self.assertIn("пройдите регистрацию", args[0])
        markup = kwargs["reply_markup"]
        button = markup.inline_keyboard[0][0]
        self.assertIn("?start=group", button.url)


class AdminStatsScopeTests(ChatModelsTestCase):
    def _callback(self):
        message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
        return SimpleNamespace(message=message, answer=AsyncMock())

    async def test_non_admin_denied_both_scopes(self) -> None:
        db_user = User(user_id=1, first_name="U", is_admin=False)
        cb = self._callback()
        async with self.sessions() as session:
            await cb_admin_stats_bot(cb, db_user, session)
        cb.answer.assert_awaited_once_with("❌ Нет доступа.", show_alert=True)

    async def test_admin_sees_chat_stats_with_top_chats(self) -> None:
        db_user = User(user_id=2, first_name="Admin", is_admin=True)
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="Big Chat", status="active", member_count=500))
            await session.commit()

        cb = self._callback()
        async with self.sessions() as session:
            await cb_admin_stats_chats(cb, db_user, session)
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Big Chat", rendered)
        self.assertIn("roulette", rendered)


if __name__ == "__main__":
    unittest.main()
