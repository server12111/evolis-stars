import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.services.referral import sponsors_word
from bot.handlers.earn import cb_earn


class SponsorsWordTests(unittest.TestCase):
    def test_russian_plural_agreement(self) -> None:
        cases = {
            1: "спонсор",
            2: "спонсора",
            3: "спонсора",
            4: "спонсора",
            5: "спонсоров",
            6: "спонсоров",
            10: "спонсоров",
            11: "спонсоров",
            12: "спонсоров",
            14: "спонсоров",
            21: "спонсор",
            22: "спонсора",
            25: "спонсоров",
        }
        for n, expected in cases.items():
            with self.subTest(n=n):
                self.assertEqual(sponsors_word(n), expected)


class EarnTextRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_min_sponsors_uses_correct_grammar_for_two(self) -> None:
        callback = SimpleNamespace(
            message=SimpleNamespace(
                delete=AsyncMock(),
                answer_photo=AsyncMock(),
                edit_text=AsyncMock(),
                answer=AsyncMock(),
            ),
            answer=AsyncMock(),
        )
        db_user = SimpleNamespace(user_id=1, referrals_count=0, stars_balance=0)
        session = SimpleNamespace()

        with (
            patch("bot.handlers.earn.ContentRepository.get_text", AsyncMock(
                return_value=(
                    "Минимум на {min_sponsors} {min_sponsors_word}."
                )
            )),
            patch("bot.handlers.earn.ContentRepository.get_photo", AsyncMock(return_value=None)),
            patch(
                "bot.handlers.earn.UserRepository.inactive_rewarded_referrals",
                AsyncMock(return_value=([], 0, 0)),
            ),
            patch("bot.handlers.earn.get_referral_reward", AsyncMock(return_value=Decimal("4"))),
            patch("bot.handlers.earn.get_min_sponsors_for_reward", AsyncMock(return_value=2)),
            patch("bot.handlers.earn.get_milestone_bonus", AsyncMock(return_value=Decimal("0.1"))),
        ):
            await cb_earn(callback, db_user, session)

        rendered = callback.message.edit_text.await_args.args[0]
        self.assertIn("2 спонсора.", rendered)
        self.assertNotIn("2 спонсоров", rendered)


if __name__ == "__main__":
    unittest.main()
