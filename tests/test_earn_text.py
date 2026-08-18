import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database.repositories.content import DEFAULT_TEXTS
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
            patch("bot.handlers.earn.get_recurring_tier_rate", AsyncMock(return_value=Decimal("1"))),
        ):
            await cb_earn(callback, db_user, session)

        rendered = callback.message.edit_text.await_args.args[0]
        self.assertIn("2 спонсора.", rendered)
        self.assertNotIn("2 спонсоров", rendered)

    async def test_default_template_shows_all_reward_tiers(self) -> None:
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

        async def reward_side_effect(_session, sponsor_count, is_premium=False):
            if is_premium:
                return Decimal("15")
            if sponsor_count <= 3:
                return Decimal("0")
            if sponsor_count <= 5:
                return Decimal("6")
            if sponsor_count <= 7:
                return Decimal("9")
            if sponsor_count <= 9:
                return Decimal("12")
            if sponsor_count <= 12:
                return Decimal("15")
            return Decimal("18")

        with (
            patch("bot.handlers.earn.ContentRepository.get_text", AsyncMock(
                return_value=DEFAULT_TEXTS["earn"]
            )),
            patch("bot.handlers.earn.ContentRepository.get_photo", AsyncMock(return_value=None)),
            patch(
                "bot.handlers.earn.UserRepository.inactive_rewarded_referrals",
                AsyncMock(return_value=([], 0, 0)),
            ),
            patch("bot.handlers.earn.get_referral_reward", AsyncMock(side_effect=reward_side_effect)),
            patch("bot.handlers.earn.get_min_sponsors_for_reward", AsyncMock(return_value=3)),
            patch("bot.handlers.earn.get_milestone_bonus", AsyncMock(return_value=Decimal("0.1"))),
            patch("bot.handlers.earn.get_recurring_tier_rate", AsyncMock(return_value=Decimal("1"))),
        ):
            await cb_earn(callback, db_user, session)

        rendered = callback.message.edit_text.await_args.args[0]
        # min_sponsors=3 -> min_sponsors_minus_1=2 ("0-2 not counted"),
        # dynamically tied to the admin-configurable gate setting.
        self.assertIn("0-2 спонсоров — не засчитывается", rendered)
        self.assertIn("4-5 спонсоров: <b>6 RP⭐️</b>", rendered)
        self.assertIn("6-7 спонсоров: <b>9 RP⭐️</b>", rendered)
        self.assertIn("8-9 спонсоров: <b>12 RP⭐️</b>", rendered)
        self.assertIn("10-12 спонсоров: <b>15 RP⭐️</b>", rendered)
        self.assertIn("13+ спонсоров: <b>18 RP⭐️</b>", rendered)
        self.assertIn("<b>15 RP⭐️</b>", rendered)  # premium line
        self.assertIn("1 Star⭐ = 3 RP⭐️", rendered)

    async def test_admin_custom_template_using_old_placeholder_names_still_renders(self) -> None:
        """An owner's hand-edited 'earn' template stored before this
        session's tier restructuring may still reference the OLD 3-tier
        placeholder names ({referral_reward}/{referral_reward_above_5}/
        {referral_reward_top}) -- these must keep resolving (to sensible
        new-tier equivalents) rather than silently falling back to
        DEFAULT_TEXTS and discarding the owner's customization."""
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

        async def reward_side_effect(_session, sponsor_count, is_premium=False):
            if is_premium:
                return Decimal("15")
            if sponsor_count <= 7:
                return Decimal("9")
            return Decimal("18")

        custom_template = (
            "Base: {referral_reward} Above5: {referral_reward_above_5} "
            "Top: {referral_reward_top} Link: {link}"
        )

        with (
            patch("bot.handlers.earn.ContentRepository.get_text", AsyncMock(return_value=custom_template)),
            patch("bot.handlers.earn.ContentRepository.get_photo", AsyncMock(return_value=None)),
            patch(
                "bot.handlers.earn.UserRepository.inactive_rewarded_referrals",
                AsyncMock(return_value=([], 0, 0)),
            ),
            patch("bot.handlers.earn.get_referral_reward", AsyncMock(side_effect=reward_side_effect)),
            patch("bot.handlers.earn.get_min_sponsors_for_reward", AsyncMock(return_value=3)),
            patch("bot.handlers.earn.get_milestone_bonus", AsyncMock(return_value=Decimal("0.1"))),
            patch("bot.handlers.earn.get_recurring_tier_rate", AsyncMock(return_value=Decimal("1"))),
        ):
            await cb_earn(callback, db_user, session)

        rendered = callback.message.edit_text.await_args.args[0]
        # Never silently discarded in favor of DEFAULT_TEXTS.
        self.assertIn("Base:", rendered)
        self.assertIn("Above5: 9", rendered)
        self.assertIn("Top: 18", rendered)


if __name__ == "__main__":
    unittest.main()
