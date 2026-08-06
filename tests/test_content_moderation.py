import unittest

from bot.services.content_moderation import find_banned_term


class FalsePositiveRegressionTests(unittest.TestCase):
    """Real collisions found by hand-testing: short banned stems that are
    also substrings of completely unrelated, common words. Each of these
    must NOT be flagged."""

    def test_kill_does_not_match_skill(self) -> None:
        self.assertIsNone(find_banned_term("прокачай свой skill в игре"))

    def test_anal_does_not_match_analytics_or_analysis(self) -> None:
        self.assertIsNone(find_banned_term("смотри нашу analytics панель"))
        self.assertIsNone(find_banned_term("полный анализ рынка"))

    def test_anal_does_not_match_canal(self) -> None:
        self.assertIsNone(find_banned_term("Panama canal trip"))

    def test_oral_does_not_match_moral_coral_floral(self) -> None:
        self.assertIsNone(find_banned_term("moral support для всех"))
        self.assertIsNone(find_banned_term("coral reef trip giveaway"))
        self.assertIsNone(find_banned_term("floral shop discount"))

    def test_anal_does_not_match_kanal_the_bots_own_domain_word(self) -> None:
        self.assertIsNone(find_banned_term("подпишись на наш канал скидки 50%"))
        self.assertIsNone(find_banned_term("подпишись на канал и получи бонус"))

    def test_sex_does_not_match_essex_unisex_sextant(self) -> None:
        self.assertIsNone(find_banned_term("Essex county trip"))
        self.assertIsNone(find_banned_term("unisex одежда скидка"))
        self.assertIsNone(find_banned_term("sextant навигация история"))

    def test_ordinary_bot_vocabulary_never_flagged(self) -> None:
        self.assertIsNone(find_banned_term(
            "баланс вывод звезды чат спонсор реферал бонус рулетка "
            "ставка приз топ профиль обмен донат задание"
        ))


class GenuineMatchTests(unittest.TestCase):
    """The false-positive fixes above must not come at the cost of missing
    genuine explicit content, including deliberately obfuscated spellings."""

    def test_kill_still_matches_as_a_whole_word(self) -> None:
        self.assertEqual(find_banned_term("я тебя убью"), "убью")
        self.assertIsNotNone(find_banned_term("killer instinct"))

    def test_anal_still_matches_standalone(self) -> None:
        self.assertIsNotNone(find_banned_term("чистый анал"))

    def test_sex_still_matches_standalone(self) -> None:
        self.assertIsNotNone(find_banned_term("секс знакомства рядом"))
        self.assertIsNotNone(find_banned_term("sex chat here"))

    def test_dotted_spacing_evasion_still_caught(self) -> None:
        self.assertEqual(find_banned_term("п.о.р.н.о видео тут"), "порно")
        self.assertEqual(find_banned_term("п о р н о"), "порно")
        self.assertEqual(find_banned_term("с-е-к-с знакомства"), "секс")

    def test_latin_cyrillic_homoglyph_mix_still_caught(self) -> None:
        self.assertEqual(find_banned_term("ceкс услуги"), "секс")

    def test_explicit_leet_variants_still_caught(self) -> None:
        self.assertIsNotNone(find_banned_term("смотри порн0 тут"))
        self.assertIsNotNone(find_banned_term("с3кс знакомства"))

    def test_drug_slang_inflected_forms_still_caught(self) -> None:
        self.assertIsNotNone(find_banned_term("купи закладку дешево"))
        self.assertIsNotNone(find_banned_term("продаю закладки"))

    def test_empty_and_none_text(self) -> None:
        self.assertIsNone(find_banned_term(""))


if __name__ == "__main__":
    unittest.main()
