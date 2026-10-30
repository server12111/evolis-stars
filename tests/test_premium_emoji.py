import unittest

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.main import main_menu_kb
from bot.services.premium_emoji import (
    _MESSAGE_EMOJI_IDS,
    ARROW_LEFT,
    ARROW_RIGHT,
    BASKETBALL,
    BLUE_DIAMOND,
    BOMB,
    BOWLING,
    CASTLE,
    CELEBRATION,
    CHECK,
    CLIPBOARD,
    CODE,
    CREDIT_CARD,
    CROWN,
    DICE,
    DIZZY_STARS,
    DOLLAR_BILL,
    ENVELOPE,
    ERROR_CROSS,
    FERRIS_WHEEL,
    FIRE,
    GAME_CONTROLLER,
    GEM,
    GIFT,
    GROWTH,
    HOME,
    INFO,
    LINK,
    LOADING,
    LOCKED,
    MEGAPHONE,
    MONEY,
    MONEY_BAG,
    MONEY_WINGS,
    PEOPLE_ICON,
    PENCIL,
    PIN,
    PLUS,
    POINT_DOWN,
    PROFILE,
    RECYCLE,
    ROCKET,
    SAD_FACE,
    SCROLL,
    SETTINGS,
    SLOT_MACHINE,
    SOCCER,
    SPARKLE,
    SPEECH_BUBBLE,
    STAR,
    SWORDS,
    TARGET,
    TICKET,
    TROPHY,
    WALLET,
    WARNING,
    WAVE,
    WOLF,
    decorate_markup,
    decorate_message_text,
)


class PremiumEmojiTests(unittest.TestCase):
    def test_main_menu_layout_and_buttons_get_premium_icons(self) -> None:
        """icon_custom_emoji_id on buttons was re-verified live (2026-08-27,
        raw sendMessage call direct to the Bot API, bypassing this bot's own
        code) to render correctly in a real private chat now that the bot
        owner's Telegram Premium condition Bot API 9.4 requires is actually
        met. decorate_markup now assigns each button's icon from its own
        leading plain emoji and strips that emoji out of the visible text
        (Telegram draws the icon separately, before the text -- leaving the
        emoji in text too would show it twice)."""
        markup = main_menu_kb()
        self.assertEqual(
            [len(row) for row in markup.inline_keyboard],
            [1, 2, 2, 2, 1, 1],
        )

        decorate_markup(markup)

        buttons = [button for row in markup.inline_keyboard for button in row]
        expected = [
            ("Заработать", MONEY_BAG),
            ("Бонус", GIFT),
            ("Задания", FIRE),
            ("Вывести", STAR),
            ("Профиль", GEM),
            ("Игры", SLOT_MACHINE),
            ("Топ", CROWN),
            ("Рандом", DICE),
            ("Донат", MONEY),
        ]
        self.assertEqual(
            [(b.text, b.icon_custom_emoji_id) for b in buttons], expected,
        )

    def test_button_with_no_matching_leading_emoji_is_untouched(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="Просто текст", callback_data="noop"),
            ]]
        )
        decorate_markup(markup)
        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.text, "Просто текст")
        self.assertIsNone(button.icon_custom_emoji_id)

    def test_emoji_only_button_keeps_text_but_still_gets_icon(self) -> None:
        """Regression: mines/tower board tiles (e.g. a revealed "💣" bomb)
        are entirely emoji, no label text. The old code refused to assign
        an icon whenever stripping the emoji would leave the button blank
        -- correct for an unmapped symbol like a captcha's "🍎", but wrong
        for a mapped one like BOMB: it meant these tiles could never show
        their premium art at all. Now the icon is assigned regardless, and
        only the TEXT-stripping is skipped so the button never goes blank."""
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="💣", callback_data="mines:reveal:0"),
            ]]
        )
        decorate_markup(markup)
        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.text, "💣")
        self.assertEqual(button.icon_custom_emoji_id, BOMB)

    def test_bare_pagination_arrow_button_stays_plain(self) -> None:
        """Regression: confirmed live that a bare "➡️" pagination button
        (bot/keyboards/earn.py's return_referrals_kb) rendered as a doubled
        arrow once it got an icon -- the icon glyph next to the kept "➡️"
        text looked like two arrows stuck together, unlike a bomb tile
        where the icon actually adds information. These stay icon-free."""
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="➡️", callback_data="earn:returns:1"),
                InlineKeyboardButton(text="⬅️", callback_data="earn:returns:0"),
            ]]
        )
        decorate_markup(markup)
        for button in markup.inline_keyboard[0]:
            self.assertIsNone(button.icon_custom_emoji_id)
        self.assertEqual(markup.inline_keyboard[0][0].text, "➡️")
        self.assertEqual(markup.inline_keyboard[0][1].text, "⬅️")

    def test_withdrawal_flow_buttons_keep_their_icon(self) -> None:
        """2026-08-28: icons were briefly stripped from the withdrawal flow
        (reported client-side lag from fetching several new-to-the-client
        icon ids back to back across its screens), then restored on the
        bot owner's explicit instruction -- premium icons matter more here
        than the one-time-per-client fetch delay. No callback_data prefix
        gets special no-icon treatment any more."""
        for prefix in ("withdraw:choose:stars", "vcwithdraw:amount:5000", "cryptowithdraw:confirm"):
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⭐️ Telegram Stars", callback_data=prefix),
                ]]
            )
            decorate_markup(markup)
            button = markup.inline_keyboard[0][0]
            self.assertEqual(button.icon_custom_emoji_id, STAR, prefix)
            self.assertEqual(button.text, "Telegram Stars", prefix)

    def test_message_emoji_is_decorated_only_once(self) -> None:
        decorated = decorate_message_text("⚙️ Настройки")
        self.assertIn(f'tg-emoji emoji-id="{SETTINGS}"', decorated)
        self.assertEqual(decorate_message_text(decorated), decorated)

    def test_recycle_symbol_is_decorated(self) -> None:
        """Regression: ♻️ (recurring-referral messaging) had no entry at
        all in the catalog -- only the visually different 🔄 "refresh"
        glyph was mapped to LOADING -- so every ♻️ occurrence rendered as
        plain Unicode. Uses the dedicated RECYCLE id (not LOADING, which
        stays on its old id for the unrelated 🔄 key -- see
        test_every_new_id_dict_key_matches_its_verified_fallback)."""
        decorated = decorate_message_text("♻️ Растущая ставка")
        self.assertIn(f'tg-emoji emoji-id="{RECYCLE}"', decorated)

    def test_every_new_id_dict_key_matches_its_verified_fallback(self) -> None:
        """Regression for a live production crash (ENTITY_TEXT_INVALID):
        decorate_message_text wraps a <tg-emoji> tag around the EXACT
        matched dict KEY text, and Telegram rejects the whole message
        unless that text byte-matches the custom emoji id's own
        registered fallback -- confirmed live when a bare "4" was used
        for a digit id whose real fallback is "4️⃣". This independently
        re-verifies (against the 2026-08-27 @EvolisStarsBot.html export,
        decoded and viewed image-by-image, not just its fallback text
        column) every _MESSAGE_EMOJI_IDS key that points at one of the
        new ids, so a future edit can't silently reintroduce a mismatch
        for these specific ids."""
        verified_fallback_by_new_id = {
            SETTINGS: "⚙️",
            PROFILE: "👤",
            GROWTH: "📈",
            HOME: "🏠",
            MEGAPHONE: "📢",
            CHECK: "✔️",
            LINK: "🔗",
            GIFT: "🎁",
            RECYCLE: "♻️",
            STAR: "⭐️",
            MONEY_BAG: "💰",
            WARNING: "⚠️",
            ARROW_LEFT: "⬅️",
            ARROW_RIGHT: "➡️",
            DICE: "🎲",
            GEM: "💎",
            ROCKET: "🚀",
            CROWN: "👑",
            WAVE: "👋",
            SPARKLE: "✨️",
            # Second batch, 2026-08-27 -- came with their own fallback
            # text already attached (same trusted format as the first
            # batch's HTML export), used byte-exact as given.
            INFO: "ℹ️",
            LOADING: "🔄",
            PIN: "📌",
            FIRE: "🔥",
            SPEECH_BUBBLE: "💬",
            GAME_CONTROLLER: "🎮",
            TROPHY: "🏆",
            SCROLL: "📜",
            # Third batch, 2026-08-27 -- a 68-emoji superset export; same
            # trusted byte-exact-as-given format.
            CODE: "🔨",
            TICKET: "🎟",
            PLUS: "➕",
            TARGET: "🎯",
            BOMB: "💣",
            SWORDS: "⚔️",
            FERRIS_WHEEL: "🎡",
            BOWLING: "🎳",
            SLOT_MACHINE: "🎰",
            BASKETBALL: "🏀",
            POINT_DOWN: "👇",
            CASTLE: "🏰",
            # Fourth round, 2026-08-27 -- the bot owner explicitly
            # confirmed these ids and had the bot's own source text
            # re-skinned (❌->❌️, ⚽->⚽️, 👥->👥️) to match, rather than
            # trying to force the old bare symbol to work.
            ERROR_CROSS: "❌️",
            SOCCER: "⚽️",
            PEOPLE_ICON: "👥️",
            BLUE_DIAMOND: "🔷️",
            # Fifth round, 2026-08-27 -- same trusted byte-exact-as-given
            # format. LOCKED/PENCIL/CELEBRATION/WALLET/MONEY/DOLLAR_BILL/
            # MONEY_WINGS all replace an old id that used to be kept only
            # because no exact match existed yet.
            WOLF: "🐺",
            PENCIL: "✏️",
            LOCKED: "🔒",
            CELEBRATION: "🎉",
            WALLET: "👛",
            MONEY: "🪙",
            DOLLAR_BILL: "💵",
            MONEY_WINGS: "💸",
            # Sixth round, 2026-08-28 -- bot owner-supplied ids, replacing
            # the old unverified CLIPBOARD/ENVELOPE ids and the earlier
            # third-batch BOMB id (see BOMB's own comment), plus three
            # brand-new ones (SAD_FACE/DIZZY_STARS/CREDIT_CARD).
            CLIPBOARD: "📋",
            ENVELOPE: "✉️",
            SAD_FACE: "😞",
            DIZZY_STARS: "💫",
            CREDIT_CARD: "💳",
        }
        checked = 0
        for key, emoji_id in _MESSAGE_EMOJI_IDS.items():
            expected = verified_fallback_by_new_id.get(emoji_id)
            if expected is None:
                continue  # not one of the ids this test independently verified
            self.assertEqual(
                key, expected,
                f"key {key!r} for id {emoji_id} must be its verified fallback {expected!r}",
            )
            checked += 1
        # Guards against the dict silently losing every one of these keys
        # (which would make the loop above vacuously pass).
        self.assertGreaterEqual(checked, len(verified_fallback_by_new_id))

    def test_cross_soccer_people_are_reskinned_not_force_matched(self) -> None:
        """The batch's ids for these three have a fallback WITH a
        variation selector (❌️/⚽️/👥️), while the bot's source used to be
        bare (❌/⚽/👥). Rather than force-matching the mismatched bare
        key to the new id (which would throw ENTITY_TEXT_INVALID --
        confirmed the hard way once already this session, for the
        leaderboard digits), the bot owner had every occurrence of the
        bare symbol changed to the VS16 variant in the source itself, so
        it now byte-matches genuinely. The bare keys must be gone from
        the dict entirely (nothing left in source to trigger them), and
        the VS16 keys must point at the new ids, not any old/legacy one."""
        self.assertNotIn("❌", _MESSAGE_EMOJI_IDS)
        self.assertNotIn("⚽", _MESSAGE_EMOJI_IDS)
        self.assertNotIn("👥", _MESSAGE_EMOJI_IDS)
        self.assertEqual(_MESSAGE_EMOJI_IDS.get("❌️"), ERROR_CROSS)
        self.assertEqual(_MESSAGE_EMOJI_IDS.get("⚽️"), SOCCER)
        self.assertEqual(_MESSAGE_EMOJI_IDS.get("👥️"), PEOPLE_ICON)

    def test_no_duplicate_keys_silently_shadow_each_other(self) -> None:
        """Regression: a bulk find-and-replace over the whole bot/ tree
        (re-skinning ❌/⚽/👥/📣/🌟/🗼/📊/◀/⏭ to their new-id-matching
        symbols) was run without excluding this file itself, and silently
        turned several already-distinct dict keys into duplicates of each
        other (e.g. "🌟": STAR_LEGACY became "⭐️": STAR_LEGACY, which
        Python resolves by just keeping the LAST "⭐️" entry -- so
        STAR_LEGACY silently overwrote STAR for the bot's own currency
        star, a ~460-use regression that would have shipped unnoticed).
        A source-level scan (not just importing the module, which can't
        see this) is the only way to actually catch it."""
        import ast

        with open("bot/services/premium_emoji.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        dict_node = None
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
            elif isinstance(node, ast.AnnAssign):
                target = node.target
            if target is not None and getattr(target, "id", None) == "_MESSAGE_EMOJI_IDS":
                dict_node = node.value
                break

        self.assertIsNotNone(dict_node, "could not locate _MESSAGE_EMOJI_IDS in source")
        keys = [k.value for k in dict_node.keys if isinstance(k, ast.Constant)]
        seen: dict[str, int] = {}
        for key in keys:
            seen[key] = seen.get(key, 0) + 1
        dupes = {k: c for k, c in seen.items() if c > 1}
        self.assertEqual(dupes, {}, f"duplicate _MESSAGE_EMOJI_IDS keys silently shadow each other: {dupes}")

    def test_stray_html_in_button_text_is_stripped_not_leaked(self) -> None:
        """Regression: a button label built from free text that happens to
        already contain real markup (e.g. a <tg-emoji> tag copied in from
        an already-decorated message) must never reach Telegram as a
        button label -- buttons never parse HTML, so it would otherwise
        show up as literal raw tag text. Confirmed live: a task's 🔗 link
        icon leaked onto its list-item button this way."""
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text=f'Подписка <tg-emoji emoji-id="{LINK}">🔗</tg-emoji> тут',
                    callback_data="noop",
                )
            ]]
        )
        decorate_markup(markup)
        button = markup.inline_keyboard[0][0]
        self.assertNotIn("<", button.text)
        self.assertNotIn("tg-emoji", button.text)
        self.assertIn("Подписка", button.text)

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
