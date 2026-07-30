import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import BotSettings, User
from bot.handlers.admin.settings import cb_phone_toggle
from bot.handlers.start import msg_phone_contact
from bot.services.phone import prompt_phone
from bot.states.phone import PhoneStates


class PhoneVerificationOnceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_duplicate_prompt_is_not_sent_for_same_pending_check(self) -> None:
        message = AsyncMock()
        state = AsyncMock()
        state.get_state.return_value = PhoneStates.waiting_contact.state

        await prompt_phone(message, state)

        message.answer.assert_not_awaited()
        state.set_state.assert_not_awaited()

    async def test_contact_is_accepted_without_fsm_state_after_restart(self) -> None:
        async with self.sessions() as session:
            user = User(
                user_id=900,
                first_name="User",
                sponsor_wave=3,
                sponsors_verified=False,
            )
            session.add(user)
            await session.commit()

            message = SimpleNamespace(
                contact=SimpleNamespace(
                    user_id=user.user_id,
                    phone_number="+7 999 123-45-67",
                ),
                from_user=SimpleNamespace(id=user.user_id),
                answer=AsyncMock(),
            )
            state = AsyncMock()
            bot = AsyncMock()

            await msg_phone_contact(message, user, session, state, bot)

        async with self.sessions() as session:
            saved = await session.get(User, 900)
            self.assertTrue(saved.phone_verified)
            self.assertEqual(saved.phone_country_code, "7")

    async def test_verified_phone_can_never_be_reset_by_another_contact(self) -> None:
        async with self.sessions() as session:
            user = User(
                user_id=901,
                first_name="User",
                phone_number="79991234567",
                phone_country_code="7",
                phone_verified=True,
                sponsors_verified=True,
            )
            session.add(user)
            await session.commit()

            message = SimpleNamespace(
                contact=SimpleNamespace(
                    user_id=user.user_id,
                    phone_number="+44 123456789",
                ),
                from_user=SimpleNamespace(id=user.user_id),
                answer=AsyncMock(),
            )
            state = AsyncMock()

            await msg_phone_contact(
                message,
                user,
                session,
                state,
                AsyncMock(),
            )

        async with self.sessions() as session:
            saved = await session.get(User, 901)
            self.assertTrue(saved.phone_verified)
            self.assertEqual(saved.phone_country_code, "7")

    async def test_admin_can_disable_phone_check_from_codes_menu(self) -> None:
        async with self.sessions() as session:
            admin = User(
                user_id=902,
                first_name="Admin",
                is_admin=True,
            )
            session.add_all(
                (
                    admin,
                    BotSettings(
                        key="phone_verification_enabled",
                        value="1",
                    ),
                )
            )
            await session.commit()
            callback = SimpleNamespace(
                data="admin:phone_toggle",
                message=SimpleNamespace(
                    edit_text=AsyncMock(),
                    answer=AsyncMock(),
                ),
                answer=AsyncMock(),
            )

            await cb_phone_toggle(callback, admin, session)

            setting = await session.get(
                BotSettings,
                "phone_verification_enabled",
            )

        self.assertEqual(setting.value, "0")
        markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
        toggle = markup.inline_keyboard[0][0]
        self.assertEqual(toggle.callback_data, "admin:phone_toggle")
        self.assertIn("выключена", toggle.text)

    async def test_contact_is_not_stored_after_admin_disables_check(self) -> None:
        async with self.sessions() as session:
            user = User(
                user_id=903,
                first_name="User",
                sponsor_wave=3,
                sponsors_verified=False,
            )
            session.add_all(
                (
                    user,
                    BotSettings(
                        key="phone_verification_enabled",
                        value="0",
                    ),
                )
            )
            await session.commit()
            message = SimpleNamespace(
                contact=SimpleNamespace(
                    user_id=user.user_id,
                    phone_number="+7 999 123-45-67",
                ),
                from_user=SimpleNamespace(id=user.user_id),
                answer=AsyncMock(),
            )
            state = AsyncMock()

            await msg_phone_contact(
                message,
                user,
                session,
                state,
                AsyncMock(),
            )

        async with self.sessions() as session:
            saved = await session.get(User, 903)
            self.assertFalse(saved.phone_verified)
            self.assertIsNone(saved.phone_number)
        self.assertGreaterEqual(message.answer.await_count, 2)


if __name__ == "__main__":
    unittest.main()
