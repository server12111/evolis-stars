import unittest

from bot.handlers.top import _rank_label
from bot.services.premium_emoji import BRONZE_MEDAL, GOLD_MEDAL, NUM_4, NUM_9, NUM_10, SILVER_MEDAL


class TopRankLabelTests(unittest.TestCase):
    """Regression: ranks 4+ used to render as plain "4.", "5." etc. with no
    premium emoji at all -- the digit constants were catalogued but never
    actually added to premium_emoji.py or wired into the leaderboard."""

    def test_top_three_use_premium_medals(self) -> None:
        self.assertEqual(_rank_label(1), f'<tg-emoji emoji-id="{GOLD_MEDAL}">🥇</tg-emoji>')
        self.assertEqual(_rank_label(2), f'<tg-emoji emoji-id="{SILVER_MEDAL}">🥈</tg-emoji>')
        self.assertEqual(_rank_label(3), f'<tg-emoji emoji-id="{BRONZE_MEDAL}">🥉</tg-emoji>')

    def test_ranks_four_to_nine_use_premium_digits(self) -> None:
        # Regression: Telegram rejects a <tg-emoji> tag with
        # ENTITY_TEXT_INVALID unless its inner text matches that custom
        # emoji's own registered fallback -- a bare "4" crashed the whole
        # handler live. The real fallback is the standard keycap sequence
        # (digit + U+FE0F + U+20E3), e.g. "4️⃣", not the bare digit.
        self.assertEqual(_rank_label(4), f'<tg-emoji emoji-id="{NUM_4}">4️⃣</tg-emoji>.')
        self.assertEqual(_rank_label(9), f'<tg-emoji emoji-id="{NUM_9}">9️⃣</tg-emoji>.')

    def test_rank_ten_uses_the_owner_supplied_id(self) -> None:
        """Bot owner explicitly asked for NUM_10 here despite its own
        registered fallback being "1️⃣" (not an actual "10" glyph) -- the
        wrapped text must be that exact fallback or Telegram rejects the
        whole message (ENTITY_TEXT_INVALID), even though the artwork it
        renders is whatever NUM_10 actually is."""
        self.assertEqual(_rank_label(10), f'<tg-emoji emoji-id="{NUM_10}">1️⃣</tg-emoji>.')


if __name__ == "__main__":
    unittest.main()
