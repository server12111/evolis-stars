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

_WORD_CHARS = "a-zа-яё0-9"

# A run of 3+ single alphanumeric characters, each separated by exactly one
# space/dot/dash/underscore — the signature shape of "p o r n o" /
# "п.о.р.н.о" / "с-е-к-с" evasion. Collapsing ONLY this specific pattern
# (not every separator in the text) means ordinary multi-letter words stay
# separated by their normal spaces — "на канал" stays two real words,
# while "п о р н о" collapses into the single token "порно".
_SPACED_LETTERS_RE = re.compile(
    rf"(?<![{_WORD_CHARS}])([{_WORD_CHARS}](?:[\s.\-_]+[{_WORD_CHARS}]){{2,}})(?![{_WORD_CHARS}])",
    re.IGNORECASE,
)
_WORD_RE = re.compile(f"[{_WORD_CHARS}]+")


def _words(text: str) -> list[str]:
    """Lowercases, collapses spaced/dotted/dashed single-letter evasion
    into single tokens, folds Latin/Cyrillic homoglyphs, then splits into
    real words. Ordinary multi-letter words are never merged with their
    neighbors, so a short banned stem can never accidentally match across
    a word boundary (only within a single word, which is what inflected-
    form stems like "дроч" are meant to catch)."""
    lowered = text.lower()
    collapsed = _SPACED_LETTERS_RE.sub(
        lambda m: re.sub(r"[\s.\-_]+", "", m.group(1)), lowered,
    )
    folded = collapsed.translate(_HOMOGLYPH_FOLD)
    return _WORD_RE.findall(folded)


# Grouped by category for maintainability — the actual check flattens this
# into one set. Kept as stems rather than every literal spelling variant,
# since _words() already collapses spacing/punctuation evasion and folds
# the common look-alike letters; only genuinely distinct spellings (real
# alphabet/digit substitutions, not just separators) need their own entry.
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

def _normalize_term(term: str) -> str:
    return " ".join(_words(term))


# Short, common-English-word-shaped terms whose obvious "safe" superset
# words (skill, analytics/анализ, moral/coral/floral, Essex/unisex/sextant)
# are far more likely in ordinary broadcast text than the explicit term
# itself — these are matched as a WHOLE word only, never as a substring of
# a longer word. Every other term is intentionally substring-matched
# (within a single word — never across a word boundary) so inflected
# Russian forms like "дрочить"/"дрочу" still get caught from the "дроч"
# stem. Normalized the exact same way as the main term list below, so the
# homoglyph fold's mixed-alphabet output (e.g. "anal" -> "анаl", with a
# Latin "l") still compares equal on both sides.
_WHOLE_WORD_ONLY: frozenset[str] = frozenset(
    _normalize_term(t) for t in ("kill", "anal", "oral", "анал", "орал", "sex", "секс")
)

_NORMALIZED_BANNED_TERMS: frozenset[str] = frozenset(
    _normalize_term(term) for terms in _BANNED_TERMS.values() for term in terms
)


def find_banned_term(text: str) -> str | None:
    """Returns the matched banned term if `text` contains prohibited
    content, else None. Intended for short promotional texts (chat
    broadcasts, button labels) — not general prose."""
    if not text:
        return None
    words = _words(text)
    word_set = set(words)
    for term in _NORMALIZED_BANNED_TERMS:
        if not term:
            continue
        if term in _WHOLE_WORD_ONLY:
            if term in word_set:
                return term
        elif any(term in word for word in words):
            return term
    return None
