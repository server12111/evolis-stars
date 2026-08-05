import unittest

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.main import main_menu_kb
from bot.services.premium_emoji import (
    decorate_markup,
    decorate_message_text,
)


class PremiumEmojiTests(unittest.TestCase):
    def test_main_menu_layout_and_icons(self) -> None:
        markup = main_menu_kb()
        self.assertEqual(
            [len(row) for row in markup.inline_keyboard],
            [1, 2, 2, 2, 1, 2],
        )

        decorate_markup(markup)
        for row in markup.inline_keyboard:
            for button in row:
                self.assertIsNotNone(button.icon_custom_emoji_id)
                self.assertTrue(button.text)

    def test_message_emoji_is_decorated_only_once(self) -> None:
        decorated = decorate_message_text("⚙️ Настройки")
        self.assertIn('tg-emoji emoji-id="5870982283724328568"', decorated)
        self.assertEqual(decorate_message_text(decorated), decorated)

    def test_visual_captcha_button_keeps_playable_symbol(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="🍎",
                    callback_data="captcha:🍎",
                )
            ]]
        )
        decorate_markup(markup)
        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.text, "🍎")
        self.assertIsNone(button.icon_custom_emoji_id)


if __name__ == "__main__":
    unittest.main()
