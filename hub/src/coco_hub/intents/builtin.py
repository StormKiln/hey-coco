"""Built-in intents: the time and the date (in the Hub's timezone), and spelling a word.

Spelling is deterministic on purpose: the Qwen spike found LLMs misspell words when asked
to spell them (docs/spikes/qwen.md), and kids ask a lot.
"""

import re
from datetime import datetime

from coco_hub.intents.engine import (
    Intent,
    IntentContext,
    IntentRegistry,
    LlmSlot,
    Match,
    Reply,
)
from coco_hub.intents.normalize import strip_filler, tokenize
from coco_hub.protocol import AnswerView, UtteranceRequest

_NOW = r"(?: now| right now)?"

# One to three words; lazy so a trailing "for me" isn't swallowed into the word.
_WORD = r"(?:the word )?(?P<word>[a-z0-9]+(?: [a-z0-9]+){0,2}?)"
_FOR_ME = r"(?: for me)?"
SPELL_PATTERNS = [
    re.compile(p)
    for p in (
        rf"how (?:do|would|should|can) (?:you|i|we) spell {_WORD}{_FOR_ME}",
        rf"how (?:to|you) spell {_WORD}{_FOR_ME}",
        rf"how is {_WORD} spel(?:led|t)",
        rf"(?:what is )?(?:the )?spelling (?:of|for) {_WORD}",
        rf"spell (?:out )?{_WORD}{_FOR_ME}",
    )
]


def spoken_time(dt: datetime) -> str:
    """``3:05 PM``, ``12:00 AM``."""
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def spoken_date(dt: datetime) -> str:
    """``Saturday, November 14th``."""
    return f"{dt:%A}, {dt:%B} {ordinal(dt.day)}"


def answer(text: str) -> Reply:
    return Reply(say=text, view=AnswerView(text=text))


def time_now(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    return answer(f"It's {spoken_time(ctx.now())}.")


def date_today(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    return answer(f"It's {spoken_date(ctx.now())}.")


def spelled(word: str) -> str:
    """``Necessary is spelled N, E, C, E, S, S, A, R, Y.``; words are separated by "space"."""
    words = ["".join(ch for ch in w if ch.isalnum()) for w in word.lower().split()]
    words = [w for w in words if w]
    if not words:
        return "Sorry, I didn't catch the word."
    letters = ", space, ".join(", ".join(ch.upper() for ch in w) for w in words)
    phrase = " ".join(words)
    return f"{phrase[0].upper()}{phrase[1:]} is spelled {letters}."


def _word_to_spell(match: Match, request: UtteranceRequest) -> str:
    """The word as said. Normalisation turns number words into digits ("how do you spell
    one" -> "1"), so a rules slot with digits is re-read from the raw transcript."""
    word = match.slots.get("word", "")
    if any(ch.isdigit() for ch in word):
        raw = " ".join(strip_filler(tokenize(request.text)))
        for pattern in SPELL_PATTERNS:
            m = pattern.fullmatch(raw)
            if m is not None:
                return m.group("word")
    return word


def spell_word(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    return answer(spelled(_word_to_spell(match, request)))


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "time.now",
            patterns=[
                rf"(?:tell me )?what time is it{_NOW}",
                rf"(?:tell me )?what time it is{_NOW}",
                rf"what is the time{_NOW}",
                rf"(?:tell me |what )?(?:the )?time{_NOW}",
                rf"what does the clock say{_NOW}",
            ],
            handler=time_now,
            description="the current time",
        )
    )
    registry.register(
        Intent(
            "date.today",
            patterns=[
                r"what is (?:the date|todays date)(?: today)?",
                r"(?:tell me )?what (?:day|date) (?:is it|it is)(?: today)?",
                r"what day is today",
                r"what is today",
                r"(?:tell me )?(?:the date|todays date)(?: today)?",
            ],
            handler=date_today,
            description="today's date or day of the week",
        )
    )
    registry.register(
        Intent(
            "spell.word",
            patterns=SPELL_PATTERNS,
            handler=spell_word,
            description='spell a word letter by letter ("how do you spell X")',
            llm_slots={
                "word": LlmSlot(
                    required=True, hint="word is the word to spell, exactly as they said it."
                )
            },
        )
    )
