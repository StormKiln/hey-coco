"""Utterance normalisation: turn a raw transcript into the canonical text intents match.

``normalize("Hey Coco, can you set a timer for a minute and a half?")``
→ ``"set a timer for 1.5 minute"``.

Steps, in order (each a pure function, and ``normalize`` is idempotent):

1. lowercase, expand contractions (``what's`` → ``what is``), drop punctuation while keeping
   digit forms like ``1.5``, ``3:30`` and ``1,000`` (→ ``1000``); ``%`` → ``percent``;
2. drop filler: a leading wake phrase / politeness (``hey coco``, ``can you``, ``do you know``)
   and ``please`` / ``um`` / ``uh`` anywhere;
3. number words → digits (``twenty five`` → ``25``), with ``a``/``an`` read as 1 only before a
   unit of time or a scale (``a minute`` → ``1 minute``, ``a timer`` stays);
4. fractions of a unit → decimals (``an hour and a half`` → ``1.5 hour``,
   ``half an hour`` → ``0.5 hour``, ``three quarters of an hour`` → ``0.75 hour``).

Unit words are left as spoken (``1.5 minute``); ``durations.parse_duration`` reads them.
"""

import re

# --- 1. text cleanup ---------------------------------------------------------------------

_CONTRACTIONS = {
    "what's": "what is",
    "whats": "what is",
    "it's": "it is",
    "that's": "that is",
    "there's": "there is",
    "here's": "here is",
    "how's": "how is",
    "where's": "where is",
    "who's": "who is",
    "when's": "when is",
    "let's": "let us",
    "i'm": "i am",
    "you're": "you are",
    "we're": "we are",
    "they're": "they are",
    "can't": "cannot",
    "won't": "will not",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "isn't": "is not",
    "aren't": "are not",
}
_CONTRACTION_RE = re.compile(r"\b(" + "|".join(map(re.escape, _CONTRACTIONS)) + r")(?![\w'])")
# A digit form (1.5, 3:30) or a plain word.
_TOKEN_RE = re.compile(r"\d+(?:[.:]\d+)+|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, expand contractions and split into words, dropping punctuation."""
    text = text.lower().replace("’", "'").replace("‘", "'")
    text = _CONTRACTION_RE.sub(lambda m: _CONTRACTIONS[m.group(1)], text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = text.replace("%", " percent ").replace("'", "")
    return _TOKEN_RE.findall(text)


# --- 2. filler ---------------------------------------------------------------------------

_FILLER_ANYWHERE = {"please", "um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm"}
# Stripped repeatedly from the start, longest first.
_LEADING_FILLER = sorted(
    (
        tuple(phrase.split())
        for phrase in (
            "hey coco",
            "hey cocoa",
            "hey koko",
            "hi coco",
            "ok coco",
            "okay coco",
            "coco",
            "cocoa",
            "koko",
            "ok",
            "okay",
            "so",
            "can you",
            "could you",
            "would you mind",
            "would you",
            "will you",
            "do you know",
        )
    ),
    key=len,
    reverse=True,
)


def strip_filler(words: list[str]) -> list[str]:
    """Drop wake phrases / politeness at the start and hesitation words anywhere."""
    words = [w for w in words if w not in _FILLER_ANYWHERE]
    stripped = True
    while stripped:
        stripped = False
        for phrase in _LEADING_FILLER:
            if tuple(words[: len(phrase)]) == phrase:
                words = words[len(phrase) :]
                stripped = True
                break
    return words


# --- 3. number words ---------------------------------------------------------------------

_ONES = {
    w: i
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen".split()
    )
}
_TENS = {
    w: (i + 2) * 10
    for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())
}
_SCALES = {"hundred": 100, "thousand": 1000}
# Words after which "a"/"an" is a quantity of 1.
_QUANTITY_NOUNS = {
    "second",
    "seconds",
    "sec",
    "secs",
    "minute",
    "minutes",
    "min",
    "mins",
    "hour",
    "hours",
    "hr",
    "hrs",
    "day",
    "days",
    "week",
    "weeks",
}
# "the next one", "which one": "one" is a pronoun, not a number.
_PRONOUN_ONE_AFTER = {"the", "this", "that", "which", "next", "last", "another", "each", "every"}
_NUMBER_WORDS = _ONES.keys() | _TENS.keys()


def _parse_number(words: list[str], start: int) -> tuple[int, int] | None:
    """Parse a spoken number at ``words[start]``; return (value, index after it)."""
    total = current = 0
    last: str | None = None  # kind of the last word consumed
    j = start
    n = len(words)
    if words[start] in ("a", "an") and start + 1 < n and words[start + 1] in _SCALES:
        current, last, j = 1, "ones", start + 1
    while j < n:
        w = words[j]
        if w in _ONES:
            value = _ONES[w]
            if last in (None, "hundred", "thousand", "and") or (last == "tens" and value < 10):
                current += value
                last = "ones" if value < 10 else "teens"
            else:
                break
        elif w in _TENS:
            if last not in (None, "hundred", "thousand", "and"):
                break
            current += _TENS[w]
            last = "tens"
        elif w == "hundred":
            if not (last in ("ones", "teens", "tens") and 0 < current < 100):
                break
            current *= 100
            last = "hundred"
        elif w == "thousand":
            if not (last in ("ones", "teens", "tens", "hundred") and total == 0 and current):
                break
            total, current = current * 1000, 0
            last = "thousand"
        elif w == "and":
            joins_more = j + 1 < n and words[j + 1] in _NUMBER_WORDS
            if not (last in ("hundred", "thousand") and joins_more):
                break
            last = "and"
        else:
            break
        j += 1
    if last is None:
        return None
    return total + current, j


def words_to_numbers(words: list[str]) -> list[str]:
    """Replace spoken numbers with digits; ``a``/``an`` before a unit of time becomes ``1``."""
    out: list[str] = []
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        nxt = words[i + 1] if i + 1 < n else None
        if w in ("a", "an") and nxt == "couple":
            out.append("2")
            i += 3 if i + 2 < n and words[i + 2] == "of" else 2
            continue
        if w in ("a", "an") and nxt in _QUANTITY_NOUNS:
            out.append("1")
            i += 1
            continue
        if w == "one" and out and out[-1] in _PRONOUN_ONE_AFTER and nxt not in _SCALES:
            out.append(w)
            i += 1
            continue
        if w in _NUMBER_WORDS or (w in ("a", "an") and nxt in _SCALES):
            parsed = _parse_number(words, i)
            if parsed is not None:
                value, i = parsed
                out.append(str(value))
                continue
        out.append(w)
        i += 1
    return out


# --- 4. fractions ------------------------------------------------------------------------

_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)"
_FRACTIONS = {"a half": 0.5, "a quarter": 0.25, "3 quarters": 0.75}
_FRACTION = r"(a half|a quarter|3 quarters)"


def _fmt(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


_FRACTION_RULES: list[tuple[re.Pattern[str], object]] = [
    # "1 hour and a half" → "1.5 hour"
    (
        re.compile(rf"\b({_NUM}) ({_UNIT}) and {_FRACTION}\b"),
        lambda m: f"{_fmt(float(m[1]) + _FRACTIONS[m[3]])} {m[2]}",
    ),
    # "2 and a half minutes" → "2.5 minutes"
    (
        re.compile(rf"\b({_NUM}) and {_FRACTION} ({_UNIT})\b"),
        lambda m: f"{_fmt(float(m[1]) + _FRACTIONS[m[2]])} {m[3]}",
    ),
    # "half 1 hour" (from "half an hour"), "a half hour" → "0.5 hour"
    (re.compile(rf"\b(?:a )?half (?:of )?(?:1 )?({_UNIT})\b"), lambda m: f"0.5 {m[1]}"),
    # "a quarter of 1 hour", "quarter hour" → "0.25 hour"
    (re.compile(rf"\b(?:a )?quarter (?:of )?(?:1 )?({_UNIT})\b"), lambda m: f"0.25 {m[1]}"),
    # "3 quarters of 1 hour" → "0.75 hour"
    (re.compile(rf"\b3 quarters (?:of )?(?:1 )?({_UNIT})\b"), lambda m: f"0.75 {m[1]}"),
]


def normalize_fractions(text: str) -> str:
    for pattern, replacement in _FRACTION_RULES:
        text = pattern.sub(replacement, text)  # type: ignore[arg-type]
    return text


# --- all together ------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Canonical form of an utterance for intent matching. Idempotent."""
    words = words_to_numbers(strip_filler(tokenize(text)))
    return normalize_fractions(" ".join(words))
