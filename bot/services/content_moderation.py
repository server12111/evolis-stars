import re

# Visually-identical Latin -> Cyrillic homoglyphs — the only fold applied,
# since these letters render indistinguishably and are a common evasion
# trick (e.g. "ceкс" mixes a Latin "c" into an otherwise-Cyrillic word).
# Deliberately narrow: letters like h/t/b/k/m are NOT folded even though
# some resemble Cyrillic letters, because they're common in ordinary
# English text and folding them would cause far more false positives than
# the evasion technique they'd catch.
_HOMOGLYPH_FOLD = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у",
})

_STRIP_RE = re.compile(r"[^a-zа-яё0-9]+")


def _normalize(text: str) -> str:
    """Lowercases, drops every non-alphanumeric character (collapsing
    spaced-out or dotted/dashed evasion like "п.о.р.н.о" / "с-е-к-с" /
    "п о р н о" into one token), then folds Latin/Cyrillic homoglyphs onto
    one canonical form. Applied identically to the keyword list (once, at
    import time) and to every submitted text (at check time), so matching
    stays correct regardless of which alphabet/spacing a sender used."""
    return _STRIP_RE.sub("", text.lower()).translate(_HOMOGLYPH_FOLD)


# Grouped by category for maintainability — the actual check flattens this
# into one set. Kept as normalized-then-deduplicated stems rather than every
# literal spelling variant, since _normalize() already collapses spacing/
# punctuation and folds the common look-alike letters; only genuinely
# distinct spellings (different letters, not just different separators)
# need their own entry.
#
# Deliberately excluded despite being common slang for prohibited content:
# "соль" (salt — an extremely common word on its own), bare "клад"
# (also means "treasure/deposit", too common), and "18+" (strips down to
# just "18", which collides with prices, ages, levels, dates). Including
# any of these would make ordinary broadcast text reject constantly.
_BANNED_TERMS: dict[str, list[str]] = {
    "sexual_explicit": [
        "порно", "porn", "порнография",
        "секс", "sex", "seks",
        "эротика", "эрот", "erotic", "erotica",
        "минет", "blowjob", "bj",
        "орал", "oral",
        "анал", "аналка", "anal",
        "дрочк", "дрочи", "дрочу",
        "мастурбац", "masturbat",
        "хентай", "hentai", "rule34", "r34", "nsfw", "nude", "nudes",
        "onlyfans", "онлифанс", "pornhub", "xvideos", "xnxx", "xhamster",
        "redtube", "brazzers",
    ],
    # Zero-tolerance category — kept intentionally blunt rather than
    # descriptive.
    "child_exploitation": [
        "csam", "лоли", "loli", "shota", "педофил", "pedo",
    ],
    "violence": [
        "убить", "убью", "убей", "убийство", "убийца", "kill", "killer", "murder",
    ],
    "terrorism_weapons": [
        "бомба", "взорвать", "взрыв", "bomb", "explode",
        "террор",
    ],
    "drugs": [
        "наркотик", "наркота", "закладк", "кладмен",
        "меф", "мефедрон", "амфетамин", "кокаин", "героин", "марихуана",
        "спайс",
        "mdma", "ecstasy", "cocaine", "heroin", "weed", "drug", "drugs",
    ],
    # Explicit digit/letter-substitution spellings that the homoglyph fold
    # alone wouldn't derive from the canonical forms above (fold only
    # covers a/e/o/p/c/x/y — it doesn't turn "0" into "о" or "3" into "е").
    "explicit_leet_variants": [
        "п0рно", "порн0", "p0rn", "porn0", "porno",
        "с3кс", "s3x",
        "эр0тика",
    ],
}

_NORMALIZED_BANNED_TERMS: frozenset[str] = frozenset(
    _normalize(term) for terms in _BANNED_TERMS.values() for term in terms
)

# A few short stems are substrings of common, unrelated words — plain
# substring matching alone would reject completely innocent text. Each
# gets a narrow regex instead of the default substring check.
# "анал" ⊂ "канал" ("channel") — a word that comes up constantly in a bot
# whose entire domain is managing Telegram channels/chats. Excluding just
# a "к" immediately before it removes that one collision without
# reopening the door to deliberately spaced-out evasion ("а н а л" still
# collapses to "анал" with nothing in front of it).
_TERM_OVERRIDES: dict[str, re.Pattern] = {
    "анал": re.compile(r"(?<!к)анал"),
}


def find_banned_term(text: str) -> str | None:
    """Returns the matched banned term (normalized form) if `text` contains
    prohibited content, else None. Intended for short promotional texts
    (chat broadcasts, button labels) — not general prose, where the
    aggressive separator-stripping this relies on would be more likely to
    produce false positives."""
    if not text:
        return None
    normalized = _normalize(text)
    for term in _NORMALIZED_BANNED_TERMS:
        if not term:
            continue
        override = _TERM_OVERRIDES.get(term)
        if override is not None:
            if override.search(normalized):
                return term
        elif term in normalized:
            return term
    return None
