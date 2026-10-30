import logging
import re
from collections.abc import Awaitable, Callable
from html import unescape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

logger = logging.getLogger(__name__)

# Central catalog supplied by the bot owner.
#
# 2026-08-27: re-pointed entries below that have a real match in the NEW
# pack (@EvolisStarsBot.html, 41 custom emoji, IDs verified by actually
# decoding and viewing each thumbnail image -- the fallback character
# Telegram stores per custom emoji is chosen by the pack creator and is
# NOT reliable, e.g. one entry's fallback is "😀" but the actual artwork
# is a hooded mascot holding a star).
#
# CRITICAL constraint discovered the hard way (live crash, ENTITY_TEXT_
# INVALID, on the leaderboard's rank digits): decorate_message_text below
# wraps a <tg-emoji> tag around the EXACT matched trigger text itself
# (see _decorate_plain_segment's `match.group(0)`), not some independently
# chosen fallback. Telegram then validates that exact wrapped text against
# the SPECIFIC custom emoji id's own true registered fallback and REJECTS
# THE WHOLE MESSAGE if it doesn't match -- e.g. an id whose real fallback
# is "✔️" cannot be wrapped around "✅", even though they look almost
# identical and are semantically interchangeable to a human. This is why
# a constant can only be re-pointed to a NEW id for a dict key whose exact
# text equals that NEW id's verified fallback (comment says which index in
# the source export). Where an existing key's text does NOT match, the
# constant keeps its OLD id (proven safe in production already) and the
# new id -- if used at all -- gets its own dedicated constant/key instead.
SETTINGS = "5224277972655054889"  # NEW: gear, verified fallback "⚙️" -- matches
# "⚙" (no VS16) is not byte-identical to SETTINGS' new fallback "⚙️" --
# kept on the old id for that specific key.
SETTINGS_LEGACY = "5870982283724328568"
PROFILE = "5224361015847725432"  # NEW: person (filled), verified fallback "👤" -- matches
# Old id, kept for reference -- superseded by PEOPLE_ICON below (2026-08-27
# fourth round), which re-skins every 👥 occurrence to 👥️ (its verified
# fallback) rather than trying to force the bare glyph to match.
PEOPLE = "5870772616305839506"
PERSON_APPROVED = "5891207662678317861"
# ФЕЙК badge's own verified fallback is "👎", not "ФЕЙК" -- see FAKE_BADGE_RU
# below and referral.py's notify_fraud_sponsors for the correct usage.
PERSON_REJECTED = "5893192487324880883"
FILE = "5870528606328852614"
SMILE = "5870764288364252592"
GROWTH = "5224351111653139458"  # NEW: bar chart w/ upward arrow, verified fallback "📈" -- matches
# Old id -- unused now. Was kept for the bare "📊" key (a different,
# bars-only glyph GROWTH's new fallback doesn't match), but the bot owner
# had every 📊 occurrence re-skinned to 📈 (2026-08-27 fourth round), so
# nothing triggers this any more.
STATISTICS = "5870921681735781843"
HOME = "5224409982769869331"  # NEW: house, verified fallback "🏠" -- matches
# "🏘" (houses) is a different glyph from "🏠" (house) -- kept on the old
# id for that specific key.
HOME_LEGACY = "5873147866364514353"
LOCKED = "5226502688404972415"  # NEW (2026-08-27 fourth batch): verified fallback "🔒" -- matches
UNLOCKED = "6037496202990194718"
MEGAPHONE = "5224450475721531885"  # NEW: megaphone, verified fallback "📢" -- matches
# Old id -- unused now. Was kept for the "📣" (loudspeaker, a different
# glyph) key, but the bot owner had every 📣 occurrence re-skinned to 📢
# (2026-08-27 fourth round), so nothing triggers this any more.
MEGAPHONE_LEGACY = "6039422865189638057"
CHECK = "5226679293165216720"  # NEW: thick checkmark, verified fallback "✔️" -- matches
# Old id -- unused now. Was kept for the "✅" (white heavy check mark, a
# different glyph, 177 uses -- the highest-traffic case in the whole
# catalog) key, but the bot owner had every ✅ occurrence re-skinned to
# ✔️ (2026-08-27 fourth round), so nothing triggers this any more.
CHECK_LEGACY = "5870633910337015697"
# Old id -- unused now. Was kept for the bare "❌" key (no matching
# fallback existed in earlier batches), but the bot owner had every ❌
# occurrence re-skinned to ❌️ (2026-08-27 fourth round, see ERROR_CROSS
# below), so nothing triggers this any more.
CROSS = "5870657884844462243"
PENCIL = "5226595189115625543"  # NEW (2026-08-27 fourth batch): verified fallback "✏️" -- only safe under an "✏️" key
# "🖋" and bare "✏" (no VS16) don't byte-match PENCIL's new fallback --
# kept on the old id for those specific keys.
PENCIL_LEGACY = "5870676941614354370"
TRASH = "5870875489362513438"
DOWN = "5893057118545646106"
PAPERCLIP = "6039451237743595514"
LINK = "5226832640677551956"  # NEW: chain links, verified fallback "🔗" -- matches
INFO = "5226564767862275557"  # NEW (2026-08-27 second batch): verified fallback "ℹ️" -- only safe under an "ℹ️" key
# Bare "ℹ" (no VS16) is not byte-identical to INFO's new fallback "ℹ️" --
# kept on the old id for that specific key.
INFO_LEGACY = "6028435952299413210"
# NEW bot-icon candidate's fallback is "😀" (it's the bot's mascot artwork,
# not a robot face) -- "🤖" doesn't match, stays on the old id.
BOT_ICON = "6030400221232501136"
EYE = "6037397706505195857"
HIDDEN = "6037243349675544634"
UPLOAD = "5963103826075456248"
DOWNLOAD = "6039802767931871481"
NOTIFICATION = "6039486778597970865"
GIFT = "5224446266653579710"  # NEW: gift box, verified fallback "🎁" -- matches
CLOCK = "5983150113483134607"
CELEBRATION = "5226628251773868414"  # NEW (2026-08-27 fourth batch): verified fallback "🎉" -- matches
WRITE = "5870753782874246579"
MEDIA = "6035128606563241721"
LOCATION = "6042011682497106307"
WALLET = "5224350849660138496"  # NEW (2026-08-27 fourth batch): verified fallback "👛" -- matches
BOX = "5884479287171485878"
CRYPTOBOT = "5260752406890711732"
CALENDAR = "5890937706803894250"
TAG = "5886285355279193209"
ELAPSED = "5775896410780079073"
# Dead code now -- was only ever reached via _BUTTON_KEYWORDS (removed,
# see _decorate_button's own docstring for why: icon_custom_emoji_id was
# confirmed live to render the wrong icon on buttons). Left defined,
# unused, in case a future real button-icon mechanism needs it.
APPS = "5224614139745314168"  # NEW: die (6 pips) -- games/casino section
BRUSH = "6050679691004612757"
ADD_TEXT = "5771851822897566479"
FORMAT = "5778479949572738874"
MONEY = "5226770767378688325"  # NEW (2026-08-27 fourth batch): verified fallback "🪙" -- matches, 22 uses
SEND_MONEY = "5224380154221995303"  # NEW: $ -- keyword-matched only, safe
RECEIVE_MONEY = "5224380154221995303"  # NEW: $ -- keyword-matched only, safe
CODE = "5224204417045145695"  # NEW (2026-08-27 third batch): verified fallback "🔨" -- matches
# The FIRST new pack's recycle id has fallback "♻️", not "🔄" -- but a
# SECOND batch (2026-08-27) included a "refresh" id whose own verified
# fallback genuinely is "🔄", so LOADING itself can finally move too.
LOADING = "5226923303142204815"  # NEW (second batch): verified fallback "🔄" -- matches
RECYCLE = "5226472688058411001"  # NEW: recycle/loop arrows, verified fallback "♻️" -- matches
SUBSCRIBE = "5224450475721531885"  # NEW: same megaphone -- keyword-matched only, safe
VERIFY = "5226679293165216720"  # NEW: same checkmark -- keyword-matched only, safe

# Sourced from feAutoSender's premium emoji catalog (same reusable
# custom-emoji IDs — Telegram custom emoji entities render for any bot,
# they aren't exclusive to whoever uploaded the pack).
STAR = "5224483169012589725"  # NEW: the RP⭐️ currency star, verified fallback "⭐️" -- matches, ~460 uses
# Old id -- unused now. Was kept for the "🌟" (glowing star, a different
# glyph) key, but the bot owner had every 🌟 occurrence re-skinned to ⭐️
# (2026-08-27 fourth round), so nothing triggers this any more.
STAR_LEGACY = "5438496463044752972"
MONEY_BAG = "5224380154221995303"  # NEW: $, verified fallback "💰" -- matches
MONEY_WINGS = "5224299353002257067"  # NEW (2026-08-27 fourth batch): verified fallback "💸" -- matches
WARNING = "5224565013909381789"  # NEW: warning triangle, verified fallback "⚠️" -- matches
ARROW_LEFT = "5224562114806458118"  # NEW: left arrow, verified fallback "⬅️" -- matches
# Old id -- unused now. Was kept for the "◀️"/"◀" (a triangle, not an
# arrow) keys, but the bot owner had every occurrence re-skinned to ⬅️
# (2026-08-27 fourth round), so nothing triggers this any more.
ARROW_LEFT_LEGACY = "5213358684024877471"
ARROW_RIGHT = "5224397136522684805"  # NEW: right arrow, verified fallback "➡️" -- matches
TICKET = "5224336109332374398"  # NEW (third batch): verified fallback "🎟" (bare, no VS16) -- matches the 19 bare uses
# The 1 "🎟️" (WITH VS16) occurrence doesn't match TICKET's new bare
# fallback -- kept on the old id for that specific key.
TICKET_LEGACY = "5431653802753159903"
# Sixth round, 2026-08-28 -- bot owner-supplied id, verified fallback "📋"
# -- matches, replaces the old (unverified) id.
CLIPBOARD = "5226736338920842771"
PLUS = "5224637805015116845"  # NEW (third batch): verified fallback "➕" -- matches
DICE = "5224614139745314168"  # NEW: die (6 pips), verified fallback "🎲" -- matches
TARGET = "5224253822053952249"  # NEW (third batch): verified fallback "🎯" -- matches
PIN = "5226679108481622679"  # NEW (2026-08-27 second batch): verified fallback "📌" -- matches
FIRE = "5226588424542136785"  # NEW (2026-08-27 second batch): verified fallback "🔥" -- matches
GEM = "5224643573156195838"  # NEW: diamond, verified fallback "💎" -- matches
ROCKET = "5226880967649569345"  # NEW: rocket, verified fallback "🚀" -- matches
HANDSHAKE = "5395732581780040886"
LOCK_KEY = "5821453562680448557"
QUESTION = "5436113877181941026"
FORBIDDEN = "5240241223632954241"
LIGHTNING = "5456140674028019486"
DOLLAR_BILL = "5224393683368978372"  # NEW (2026-08-27 fourth batch): verified fallback "💵" -- matches
# Sixth round, 2026-08-28 -- bot owner-supplied id, verified fallback "✉️"
# -- matches, replaces the old (unverified) id.
ENVELOPE = "5226808773544290621"
# Dead code now (2026-08-28) -- was only ever reached via the "📲" key,
# which the bot owner had every occurrence re-skinned to "📱" (MOBILE)
# instead.
MOBILE_SEND = "5406809207947142040"
HOURGLASS = "5175181110572745347"
STOPWATCH = "5373236586760651455"
# Old id -- unused now. Was kept for the "⏭️"/"⏭" (a next-track symbol,
# different glyph) keys, but the bot owner had every occurrence re-skinned
# to ➡️ (2026-08-27 fourth round), so nothing triggers this any more.
SKIP = "5240148091562125943"

# Brand-new concepts introduced by the 2026-08-27 pack that have no old
# equivalent to repoint -- added as their own constants instead.
WAVE = "5224269219511705100"  # waving hand (filled) -- greetings/welcome
SPARKLE = "5224519461486241288"  # sparkles -- generic "premium/highlight" accent
CROWN = "5224319900125799159"  # crown -- VIP/top status (bot already has a "👑 Топ" button with no icon until now)
# The pack's two "this account is fake" text badges (English/Russian) --
# deliberately NOT used as a generic ❌/cross substitute (see ERROR_CROSS
# below for that). Only appropriate where
# a message is specifically about a fraud-flagged/rejected account, e.g.
# referral.py's notify_fraud_sponsors -- RU for the user/referrer-facing
# notices, EN for the internal admin-channel audit copy.
FAKE_BADGE_EN = "5224461307629053911"  # verified fallback "👎"
FAKE_BADGE_RU = "5224194345346835603"  # verified fallback "👎"

# The bot's own hooded-mascot-holding-a-star artwork -- its actual fallback
# character is "😀" (misleading; chosen by whoever made the pack, unrelated
# to what it depicts). Used once, in the /start welcome message.
MASCOT = "5224679453312984113"  # verified fallback "😀"
# "@EvolisStarsBot" wordmark artwork -- fallback is "💪", equally
# unrelated to the actual image. Used as a signature line in /start.
BOT_WORDMARK = "5226954557619218188"  # verified fallback "💪"
# 3x3 numpad grid (digits 1-9) -- distinct artwork from the individual
# NUM_1..NUM_9 above, shares NUM_1's fallback "1️⃣". Used as a decorative
# header icon on the /top leaderboard.
NUMPAD = "5226474032383173627"  # verified fallback "1️⃣"

# Second batch, 2026-08-27 -- same pack owner, IDs handed over with their
# own verified fallback text already attached (same trusted format as the
# first batch's HTML export), so used byte-exact as given rather than
# re-derived. Several fill real gaps the first batch never covered (🎮/🏆/
# 💬 had NO premium id at all before this); a few upgrade an existing
# _MESSAGE_EMOJI_IDS entry that had to stay on its old id in the first
# batch for lack of an exact fallback match (see FIRE/PIN/INFO/LOADING
# above, now moved here since a genuinely matching id showed up).
SPEECH_BUBBLE = "5226696692077731319"  # verified fallback "💬" -- 20 uses, previously unmapped
GAME_CONTROLLER = "5224708079270013673"  # verified fallback "🎮" -- 30 uses, previously unmapped
TROPHY = "5226892735859961563"  # verified fallback "🏆" -- 21 uses, previously unmapped
SCROLL = "5224571477835162080"  # verified fallback "📜" -- 9 uses, previously unmapped
# Dormant for now -- no plain-text trigger currently in the codebase for
# any of these, but cataloged and correctly matched for future use.
SEARCH = "5224212023432226159"  # verified fallback "🔎" -- code currently uses the visually different 🔍 (U+1F50D), not this one (U+1F50E)
HUNDRED = "5224326961052033400"  # verified fallback "💯"
MOBILE = "5224374248641964654"  # verified fallback "📱"
ID_ICON = "5224289556181854894"  # verified fallback "🆔️"
# Active (2026-08-27 fourth round): re-skinned from 🔹 (a different, small
# blue diamond glyph -- see the re-skin note further down).
BLUE_DIAMOND = "5224624086889572461"  # verified fallback "🔷️"

# Third batch, 2026-08-27 (a 68-emoji superset export replacing the first
# two batches, per the bot owner: "use only what I give you"). Several
# fill real gaps -- these symbols were already used heavily in the bot
# with NO premium mapping at all before now.
# Sixth round, 2026-08-28 -- bot owner re-supplied this id, verified
# fallback "💣" -- replaces the earlier third-batch id below.
BOMB = "5226939456514203548"
SWORDS = "5224626002444984627"  # verified fallback "⚔️" (WITH VS16, matches all 21 uses exactly) -- previously unmapped
FERRIS_WHEEL = "5226506510925868469"  # verified fallback "🎡" -- 12 uses, previously unmapped
BOWLING = "5226968018046725177"  # verified fallback "🎳" -- 9 uses, previously unmapped
SLOT_MACHINE = "5224465306243607087"  # verified fallback "🎰" -- 18 uses, previously unmapped
BASKETBALL = "5226886263344246384"  # verified fallback "🏀" -- 10 uses, previously unmapped
POINT_DOWN = "5226576458763248307"  # verified fallback "👇" -- 2 uses, previously unmapped
# CASTLE (🏰) has no exact-match trigger of its own -- the bot owner
# explicitly chose to repoint the 🗼 Tokyo Tower spots at this artwork
# instead (12 uses) rather than wait for a matching 🗼 id. The actual
# source text at every one of those spots was changed from 🗼 to 🏰 to
# match (see the replacement note below); this is NOT a fallback-text
# trick, it's a deliberate re-skin of what the bot displays.
CASTLE = "5224707005528188746"  # verified fallback "🏰"

# 2026-08-27, fourth round: the bot owner confirmed these three ids are
# correct DESPITE their fallback not matching the symbol the bot used to
# display -- same re-skin approach as CASTLE/🗼 above, not a fallback-text
# trick. The bot's actual source text was changed at every occurrence
# (❌->❌️, ⚽->⚽️, 👥->👥️) to genuinely match each id's own fallback,
# rather than trying to wrap the old symbol in the new id's tag.
ERROR_CROSS = "5224630585175090875"  # verified fallback "❌️" -- replaces plain ❌ (396 uses, the highest-traffic case in the whole bot)
SOCCER = "5226432830761907389"  # verified fallback "⚽️" -- replaces plain ⚽ (6 uses)
PEOPLE_ICON = "5226704646357165977"  # verified fallback "👥️" -- replaces plain 👥 (19 uses)
# 🔷️ (BLUE_DIAMOND, declared above) replaces plain 🔹 (a different, small
# blue diamond glyph) -- same re-skin approach.

# Fifth round, 2026-08-27 (same trusted byte-exact-as-given format).
WOLF = "5224491084637318426"  # verified fallback "🐺" -- the "Sigma" referral-tier badge, previously unmapped
# Leaderboard medal artwork -- exact fallback matches for MEDALS[0..2] in
# bot/handlers/top.py (🥇🥈🥉), replacing the plain versions used there.
GOLD_MEDAL = "5226486285924870418"  # verified fallback "🥇"
SILVER_MEDAL = "5226739641750693057"  # verified fallback "🥈"
BRONZE_MEDAL = "5226877982647296615"  # verified fallback "🥉"
# Bot owner explicitly asked (twice) for this to represent rank 10 in
# top.py's _rank_label, despite its own registered fallback being "1️⃣"
# (identical in form to NUM_1 above, not an actual "10" glyph) -- the tag
# still renders whatever artwork Telegram has on file for this id in any
# client that supports custom emoji; only a non-supporting client falls
# back to seeing "1️⃣" instead of "10".
NUM_10 = "5226966772506208520"  # registered fallback "1️⃣"

# Sixth round, 2026-08-28 -- profile field-label icons. PROFILE_NAME_ICON's
# fallback "👤" collides with the existing PROFILE constant's own "👤" key
# in _MESSAGE_EMOJI_IDS (different artwork, different id, same fallback
# text) -- rather than fight over that one dict slot, both of these are
# used directly via premium() in content.py's "profile" template instead
# of going through the auto-decorate dict.
PROFILE_NAME_ICON = "5224276052804673984"  # verified fallback "👤"
USERNAME_ICON = "5224541112416384107"  # verified fallback "🐕"
SAD_FACE = "5226469458243004500"  # verified fallback "😞" -- game-loss messages, previously unmapped
DIZZY_STARS = "5226826172456805748"  # verified fallback "💫" -- "Получите/Получит/Стоимость" headers, previously unmapped
CREDIT_CARD = "5226471622906520120"  # verified fallback "💳" -- lottery balance line, previously unmapped

# Individual digit emoji 1-9 (each its own custom emoji in the pack, not a
# combining keycap sequence) -- e.g. for leaderboard ranks. No "0" or "10"
# exists in the pack, so a two-digit rank (10+) has no premium form and
# must stay plain text.
NUM_1 = "5224612846960159237"
NUM_2 = "5224680174867489117"
NUM_3 = "5224223800232552543"
NUM_4 = "5224261840757893133"
NUM_5 = "5226445737138628746"
NUM_6 = "5224218659156699630"
NUM_7 = "5224645415697163934"
NUM_8 = "5224264026896243346"
NUM_9 = "5224733823303984300"
NUM_DIGITS: dict[int, str] = {
    1: NUM_1, 2: NUM_2, 3: NUM_3, 4: NUM_4, 5: NUM_5,
    6: NUM_6, 7: NUM_7, 8: NUM_8, 9: NUM_9,
}

# Pack-resident ids (verified against getStickerSet("EvolisStarsBot"),
# 2026-10-30) that supersede the older ids from other packs above. The
# bot must only ever show custom emoji from that one pack.
UNLOCKED_PACK = "5224574376938087543"  # fallback "🔓"
PAPERCLIP_PACK = "5224490496226797830"  # fallback "📎"
BOT_ICON_PACK = "5233698369238506732"  # fallback "🤖"
NOTIFICATION_PACK = "5235493828776994766"  # fallback "🔔"
LIGHTNING_PACK = "5233622344022401433"  # fallback "⚡️"
HOURGLASS_PACK = "5237752049566789242"  # fallback "⏳"
STOPWATCH_PACK = "5238067695303305800"  # fallback "⏱️"
CONSTRUCTION = "5233355154106918676"  # fallback "🚧"
ARROW_RETURN = "5233735937317447077"  # fallback "↩️"
CHART_DOWN = "5233410275717198826"  # fallback "📉"
GOAL_NET = "5235896735364067074"  # fallback "🥅"
WIND = "5233562510833021703"  # fallback "🌬"
WHITE_SQUARE = "5229110588252136623"  # fallback "⬜️"
BULB = "5224256807056221467"  # fallback "💡"

_MESSAGE_EMOJI_IDS: dict[str, str] = {
    # NOTE on the entries below marked "exact fallback match required":
    # each key's text must be byte-identical to that specific custom
    # emoji's own registered fallback (see the CRITICAL constraint note
    # above the constants block) -- a bare/VS16 variant of an otherwise-
    # matching key (e.g. "⚙" vs "⚙️") is intentionally left unmapped
    # rather than guessed at, since a wrong guess here means Telegram
    # rejects the ENTIRE message (ENTITY_TEXT_INVALID), not just that one
    # emoji.
    "⚙️": SETTINGS,  # exact match
    "👤": PROFILE,
    "📈": GROWTH,  # exact match
    "🏠": HOME,  # exact match
    "🔒": LOCKED,
    "🔓": UNLOCKED_PACK,
    "📢": MEGAPHONE,  # exact match
    "✔️": CHECK,  # exact match
    "✏️": PENCIL,  # exact match (fourth-batch id)
    "📎": PAPERCLIP_PACK,
    "🔗": LINK,  # exact match
    "ℹ️": INFO,  # exact match
    "🤖": BOT_ICON_PACK,
    "🔔": NOTIFICATION_PACK,
    "🎁": GIFT,  # exact match
    "🎉": CELEBRATION,
    "👛": WALLET,
    "🪙": MONEY,
    "🔨": CODE,  # exact match (third-batch id)
    "🔄": LOADING,  # exact match (second-batch id)
    # Gap-filled from feAutoSender's catalog.
    "⭐️": STAR,  # exact match
    "💰": MONEY_BAG,  # exact match
    "💸": MONEY_WINGS,  # exact match (fourth-batch id)
    "⚠️": WARNING,  # exact match
    "⬅️": ARROW_LEFT,  # exact match
    "➡️": ARROW_RIGHT,  # exact match
    "🎟": TICKET,  # exact match (third-batch id)
    "📋": CLIPBOARD,
    "➕": PLUS,  # exact match (third-batch id)
    "🎲": DICE,  # exact match
    "🎯": TARGET,  # exact match (third-batch id)
    "📌": PIN,  # exact match (second-batch id)
    "🔥": FIRE,  # exact match (second-batch id)
    "💎": GEM,  # exact match
    "🚀": ROCKET,  # exact match
    "⚡️": LIGHTNING_PACK,
    "💵": DOLLAR_BILL,
    "✉️": ENVELOPE,  # exact match; bare "✉" has no current use in the bot
    "⏳": HOURGLASS_PACK,
    "⏱️": STOPWATCH_PACK,
    # Gap-filled from the 2026-08-27 pack -- previously unmapped, so these
    # plain emoji rendered as ordinary Unicode everywhere they were used.
    # All three are exact fallback matches for their id.
    "👑": CROWN,
    "👋": WAVE,
    "✨️": SPARKLE,
    # ♻️ (recurring-referral messaging, earn.py/settings.py/referral.py) was
    # never mapped at all before -- only 🔄 (a visually different "refresh"
    # glyph) pointed at LOADING, so every ♻️ occurrence rendered as plain
    # Unicode regardless of the catalog above it. Exact fallback match.
    "♻️": RECYCLE,
    # Second batch, 2026-08-27 -- see the constants' own comments. All
    # exact fallback matches.
    "💬": SPEECH_BUBBLE,
    "🎮": GAME_CONTROLLER,
    "🏆": TROPHY,
    "📜": SCROLL,
    # Dormant (no current trigger in the codebase) but correctly mapped:
    "🔎": SEARCH,
    "💯": HUNDRED,
    # 📱 re-skinned from 📲 (2026-08-28) -- bot owner asked for this exact
    # artwork on the sponsor/task URL buttons (tasks.py, own_sponsor_wall.py,
    # bonus.py); the old 📲 MOBILE_SEND id/key is gone, nothing triggers it.
    "📱": MOBILE,
    # 🆔️ active (2026-08-28) -- group/owner_menu.py's chat-id line re-skinned
    # from bare 🆔 to match.
    "🆔️": ID_ICON,
    # Third batch, 2026-08-27 -- see the constants' own comments. All
    # exact fallback matches; these had NO premium mapping at all before,
    # despite real, sometimes-heavy existing use.
    "💣": BOMB,
    "⚔️": SWORDS,
    "🎡": FERRIS_WHEEL,
    "🎳": BOWLING,
    "🎰": SLOT_MACHINE,
    "🏀": BASKETBALL,
    "👇": POINT_DOWN,
    # Fourth round, 2026-08-27 -- re-skinned: the bot's own source text was
    # changed to these exact symbols (see the constants' own comments) so
    # they match each id's real fallback, rather than trying to force a
    # mismatched old symbol to work.
    "🏰": CASTLE,
    "❌️": ERROR_CROSS,
    "⚽️": SOCCER,
    "👥️": PEOPLE_ICON,
    "🔷️": BLUE_DIAMOND,  # re-skinned from 🔹, see the constant's own comment
    # Fifth round, 2026-08-27 -- exact fallback match, previously unmapped.
    "🐺": WOLF,
    # GOLD_MEDAL/SILVER_MEDAL/BRONZE_MEDAL were wired into top.py's and
    # group/info.py's own MEDALS lists directly via premium(), but 🥇🥈🥉
    # are also used as plain text/button emoji elsewhere (cases.py, duel.py,
    # keyboards/cases.py) -- adding them here covers every remaining spot
    # in one place instead of hunting down each call site individually.
    "🥇": GOLD_MEDAL,
    "🥈": SILVER_MEDAL,
    "🥉": BRONZE_MEDAL,
    # Sixth round, 2026-08-28 -- exact fallback matches, previously unmapped.
    "😞": SAD_FACE,
    "💫": DIZZY_STARS,
    "💳": CREDIT_CARD,
    # 2026-10-30: the owner's pack (t.me/addemoji/EvolisStarsBot) was
    # re-checked against the catalog -- everything above resolves to an id
    # that is IN that pack, and these are its newer exact-fallback entries.
    "🚧": CONSTRUCTION,
    "↩️": ARROW_RETURN,
    "📉": CHART_DOWN,
    "🥅": GOAL_NET,
    "🐕": USERNAME_ICON,
    "🌬": WIND,
    "⬜️": WHITE_SQUARE,
    "💡": BULB,
}
_MESSAGE_EMOJI_RE = re.compile(
    "|".join(
        re.escape(symbol)
        for symbol in sorted(_MESSAGE_EMOJI_IDS, key=len, reverse=True)
    )
)
# Bare pagination arrows -- see _decorate_button's own docstring for why
# these specifically don't get an icon when they're a button's ENTIRE text.
_NO_ICON_WHEN_BARE = {ARROW_LEFT, ARROW_RIGHT}
_CUSTOM_TAG_RE = re.compile(
    r'<tg-emoji\s+emoji-id="\d+">.*?</tg-emoji>',
    flags=re.DOTALL,
)
_CUSTOM_TAG_UNWRAP_RE = re.compile(
    r'<tg-emoji\s+emoji-id="\d+">(.*?)</tg-emoji>',
    flags=re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_stray_html(text: str) -> str:
    """Button labels are ALWAYS plain text -- Telegram never parses HTML
    entities there, unlike message bodies/captions. Several buttons build
    their label straight from admin-entered free text meant for a message
    context (task titles in bot/keyboards/tasks.py, own-sponsor names,
    ...); if that text ever contains real markup -- most likely a
    <tg-emoji> tag copied in from an already-decorated message -- it must
    never reach Telegram as a button label, or it shows up as literal raw
    tag text instead of silently doing nothing. Confirmed live: a task's
    🔗 link icon leaked onto its list-item button this way."""
    if "<" not in text:
        return text
    return unescape(_HTML_TAG_RE.sub("", text))


def premium(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'


def decorate_message_text(text: str) -> str:
    """Replace known ordinary emoji outside existing custom-emoji tags."""
    parts: list[str] = []
    cursor = 0
    for match in _CUSTOM_TAG_RE.finditer(text):
        parts.append(_decorate_plain_segment(text[cursor:match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_decorate_plain_segment(text[cursor:]))
    return "".join(parts)


def _decorate_plain_segment(text: str) -> str:
    return _MESSAGE_EMOJI_RE.sub(
        lambda match: premium(
            match.group(0),
            _MESSAGE_EMOJI_IDS[match.group(0)],
        ),
        text,
    )


def _decorate_button(button: InlineKeyboardButton | KeyboardButton) -> None:
    """Auto-assigns icon_custom_emoji_id from the button's own LEADING
    plain emoji, then strips that emoji (and the space after it) from the
    button text so it isn't shown twice -- Telegram renders the icon as a
    separate glyph before the text, it doesn't replace anything in `text`.

    Bot API 9.4 (2026-02-09) added icon_custom_emoji_id to InlineKeyboard-
    Button/KeyboardButton. It silently no-ops (Telegram just drops the
    field, no error) UNLESS the chat is private/group/supergroup (not a
    channel) AND either the bot bought a Fragment username or the bot's
    owner has an active Telegram Premium subscription. An EARLIER live
    test on this exact mechanism (back_to_menu_kb, 2026-08-27) showed the
    wrong icon and the auto-assignment was removed entirely as a result --
    that test predated confirming those conditions were actually met.
    Re-verified 2026-08-27 with a raw sendMessage call straight to the Bot
    API (bypassing this bot's own code) in a real private chat: Telegram
    accepted icon_custom_emoji_id and it rendered correctly. Restored.

    Only the leading emoji is used (one icon per button, not one per
    matched word inside the label). If stripping it would leave the button
    with no text at all (an emoji-only button, e.g. a mines/tower board
    tile), the icon is still assigned but the original emoji is kept as
    the button's text -- Telegram requires non-empty button text, and an
    icon-only button would otherwise go blank. EXCEPT for bare pagination
    arrows (_NO_ICON_WHEN_BARE): confirmed live the icon renders as a
    second, near-identical arrow glyph right next to the kept text one --
    doubled and redundant, unlike e.g. a bomb tile where the icon adds
    real information -- so these are left as plain text instead.

    2026-08-28: briefly stripped icons from the withdrawal flow entirely
    (a rapid chain of screens each introducing a new-to-the-client icon id
    was reported as laggy -- the client has to fetch each not-yet-cached
    custom emoji sticker before the button becomes interactive). Reverted
    on the bot owner's explicit instruction ("я хотел оставить премиум
    эмодзи") -- premium icons matter more than the one-time-per-client
    fetch delay, which stops recurring once a client has cached these ids."""
    try:
        text = _strip_stray_html(button.text)
    except AttributeError:
        return
    match = _MESSAGE_EMOJI_RE.match(text)
    if match:
        icon_id = _MESSAGE_EMOJI_IDS[match.group(0)]
        remainder = text[match.end():].lstrip()
        if remainder or icon_id not in _NO_ICON_WHEN_BARE:
            button.icon_custom_emoji_id = icon_id
        if remainder:
            text = remainder
    button.text = text


def decorate_markup(markup: Any) -> None:
    if isinstance(markup, InlineKeyboardMarkup):
        for row in markup.inline_keyboard:
            for button in row:
                _decorate_button(button)
    elif isinstance(markup, ReplyKeyboardMarkup):
        for row in markup.keyboard:
            for button in row:
                _decorate_button(button)


def _remove_custom_emoji(method: TelegramMethod[Any]) -> None:
    for field_name in ("text", "caption"):
        value = getattr(method, field_name, None)
        if isinstance(value, str):
            setattr(
                method,
                field_name,
                _CUSTOM_TAG_UNWRAP_RE.sub(r"\1", value),
            )
    markup = getattr(method, "reply_markup", None)
    if isinstance(markup, InlineKeyboardMarkup):
        rows = markup.inline_keyboard
    elif isinstance(markup, ReplyKeyboardMarkup):
        rows = markup.keyboard
    else:
        rows = []
    for row in rows:
        for button in row:
            button.icon_custom_emoji_id = None


class PremiumEmojiMiddleware:
    async def __call__(
        self,
        make_request: Callable[
            [Bot, TelegramMethod[Any]],
            Awaitable[Any],
        ],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        changed = False
        if type(method).__name__ != "AnswerCallbackQuery":
            for field_name in ("text", "caption"):
                value = getattr(method, field_name, None)
                if isinstance(value, str):
                    decorated = decorate_message_text(value)
                    if decorated != value:
                        setattr(method, field_name, decorated)
                        changed = True

        markup = getattr(method, "reply_markup", None)
        if isinstance(markup, (InlineKeyboardMarkup, ReplyKeyboardMarkup)):
            decorate_markup(markup)
            changed = True

        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            premium_error = any(
                marker in error_text
                for marker in (
                    "custom emoji",
                    "custom_emoji",
                    "icon_custom_emoji_id",
                    "button_type_invalid",
                    # Confirmed live: a <tg-emoji> tag whose wrapped text
                    # doesn't byte-match that id's own registered fallback
                    # (see the catalog's own CRITICAL constraint note)
                    # crashes with exactly this error, not any of the
                    # markers above -- without it, one bad mapping takes
                    # the whole handler down instead of self-healing.
                    "entity_text_invalid",
                )
            )
            if not changed or not premium_error:
                raise
            logger.warning(
                "Telegram rejected custom emoji for %s; retrying without premium entities",
                type(method).__name__,
            )
            _remove_custom_emoji(method)
            return await make_request(bot, method)
