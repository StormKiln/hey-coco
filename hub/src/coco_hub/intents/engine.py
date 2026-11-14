"""Deterministic intent matching.

An ``Intent`` declares patterns and a handler. Patterns are matched against the
*normalised* utterance (see ``normalize.py``) and must match the whole of it. A pattern is
either a regex (string or compiled; named groups become slots) or a matcher callable
``(text) -> dict[str, str] | None``.

When several intents match, the winner is the one with the highest ``priority``; ties go to
the most specific match (most characters matched literally rather than captured into slots),
then to the intent registered first.

Handlers get ``(match, request, ctx)`` and return a ``Reply``; the engine turns it into a
protocol ``Action`` stamped with the intent name and how it was matched, so handlers can't
misreport provenance and a fallback (Qwen) can reuse them via ``RulesEngine.dispatch``.
Handlers may be sync or async.

An intent is offered to the Qwen fallback when it has a ``description`` and, if its patterns
capture slots, declares ``llm_slots``: how Qwen should fill each slot and how the typed value
becomes the string slot the handler already reads (see ``LlmSlot``).
"""

import inspect
import re
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo

from coco_hub.intents.normalize import normalize
from coco_hub.protocol import Action, Command, UtteranceContext, UtteranceRequest, View
from coco_hub.settings import Settings

Slots = dict[str, str]
Matcher = Callable[[str], Mapping[str, str] | None]
PatternLike = str | re.Pattern[str] | Matcher
MatchedBy = Literal["rules", "qwen", "none"]


@dataclass(frozen=True)
class IntentContext:
    """What handlers may depend on. Build one with fakes in tests."""

    settings: Settings
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    services: Mapping[str, Any] = field(default_factory=dict)

    @property
    def tz(self) -> tzinfo:
        return ZoneInfo(self.settings.timezone)

    def now(self) -> datetime:
        """Current time in the Hub's timezone."""
        return self.clock().astimezone(self.tz)


@dataclass(frozen=True)
class Match:
    intent: str
    slots: Slots
    text: str
    """The normalised utterance that matched."""


@dataclass(frozen=True)
class NoMatch:
    text: str
    """The normalised utterance."""
    raw: str = ""
    """The utterance as received."""


@dataclass(frozen=True)
class Reply:
    """A handler's answer: an ``Action`` minus ``intent`` / ``matched_by``."""

    say: str | None
    view: View | None = None
    commands: tuple[Command, ...] = ()


@dataclass(frozen=True)
class LlmSlot:
    """A slot Qwen may fill for an intent, as a typed JSON value.

    ``slot`` is the handler's slot name (default: the LLM slot's own key) and ``to_slot``
    turns the validated value into the string the handler expects, e.g. ``duration_s=600``
    -> ``duration="600 seconds"``; returning ``""`` drops the slot (a repair, e.g. a made-up
    timer name). ``hint`` is one line added to the prompt's slot rules.
    LLM slots with the same key on different intents share one schema property, so they must
    agree on ``type``.
    """

    type: Literal["string", "integer", "boolean"] = "string"
    required: bool = False
    enum: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    hint: str = ""
    slot: str | None = None
    to_slot: Callable[[Any], str] | None = None

    def handler_slot(self, key: str, value: Any) -> tuple[str, str]:
        """(handler slot name, string value) for a validated ``value``."""
        if self.to_slot is not None:
            text = self.to_slot(value)
        elif isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        return (self.slot or key, text)


Handler = Callable[[Match, UtteranceRequest, IntentContext], Reply | Awaitable[Reply]]
Fallback = Callable[[NoMatch, UtteranceRequest, IntentContext], Awaitable[Action]]


def _compile(pattern: PatternLike) -> re.Pattern[str] | Matcher:
    if isinstance(pattern, str):
        return re.compile(pattern)
    if isinstance(pattern, re.Pattern) or callable(pattern):
        return pattern
    raise TypeError(f"pattern must be a regex or a callable, not {type(pattern).__name__}")


@dataclass(frozen=True)
class Intent:
    name: str
    patterns: Iterable[PatternLike]
    handler: Handler
    priority: int = 0
    applies: Callable[[UtteranceContext], bool] | None = None
    """Optional guard: the intent only matches when this returns True for the request's
    context (e.g. ``lambda c: c.alarm_ringing``). Without a context the intent is skipped."""
    description: str = ""
    """One line for the fallback's closed set of intents."""
    llm_slots: Mapping[str, LlmSlot] = field(default_factory=dict, hash=False)
    """Slots Qwen may fill (key = JSON slot name). See ``LlmSlot``."""

    def __post_init__(self) -> None:
        compiled = tuple(_compile(p) for p in self.patterns)
        if not compiled:
            raise ValueError(f"intent {self.name!r} needs at least one pattern")
        object.__setattr__(self, "patterns", compiled)
        object.__setattr__(self, "llm_slots", dict(self.llm_slots))

    @property
    def offered_to_llm(self) -> bool:
        """Whether the Qwen fallback may pick this intent: it needs a description, and an
        intent whose patterns capture slots must say how Qwen fills them."""
        return bool(self.description) and (bool(self.llm_slots) or not self.slot_names)

    @property
    def slot_names(self) -> tuple[str, ...]:
        """Named groups across the regex patterns, in order of first appearance."""
        names: dict[str, None] = {}
        for p in self.patterns:
            if isinstance(p, re.Pattern):
                names.update(dict.fromkeys(p.groupindex))
        return tuple(names)

    def match(self, text: str) -> tuple[Slots, int] | None:
        """Best (slots, specificity) for ``text`` across this intent's patterns."""
        best: tuple[Slots, int] | None = None
        for pattern in self.patterns:  # type: ignore[union-attr]
            if isinstance(pattern, re.Pattern):
                m = pattern.fullmatch(text)
                if m is None:
                    continue
                slots = {k: v for k, v in m.groupdict().items() if v is not None}
                captured = sum(m.end(k) - m.start(k) for k in slots)
            else:
                found = pattern(text)
                if found is None:
                    continue
                slots = dict(found)
                captured = sum(len(v) for v in slots.values())
            specificity = len(text) - captured
            if best is None or specificity > best[1]:
                best = (slots, specificity)
        return best


class IntentRegistry:
    """The set of intents the rules engine knows. Feature modules register into it."""

    def __init__(self) -> None:
        self._intents: dict[str, Intent] = {}

    def register(self, intent: Intent) -> Intent:
        if intent.name in self._intents:
            raise ValueError(f"intent {intent.name!r} already registered")
        self._intents[intent.name] = intent
        return intent

    def get(self, name: str) -> Intent | None:
        return self._intents.get(name)

    @property
    def intents(self) -> tuple[Intent, ...]:
        return tuple(self._intents.values())

    def __contains__(self, name: object) -> bool:
        return name in self._intents

    def __iter__(self) -> Iterator[Intent]:
        return iter(self.intents)

    def __len__(self) -> int:
        return len(self._intents)

    def match(self, text: str, context: UtteranceContext | None = None) -> Match | None:
        """Best match for already-normalised ``text``, or None."""
        best: tuple[tuple[int, int, int], Match] | None = None
        for order, intent in enumerate(self._intents.values()):
            if intent.applies is not None and (context is None or not intent.applies(context)):
                continue
            found = intent.match(text)
            if found is None:
                continue
            slots, specificity = found
            rank = (intent.priority, specificity, -order)
            if best is None or rank > best[0]:
                best = (rank, Match(intent=intent.name, slots=slots, text=text))
        return best[1] if best else None


class RulesEngine:
    def __init__(self, registry: IntentRegistry) -> None:
        self.registry = registry

    async def handle(self, request: UtteranceRequest, ctx: IntentContext) -> Action | NoMatch:
        text = normalize(request.text)
        match = self.registry.match(text, request.context)
        if match is None:
            return NoMatch(text=text, raw=request.text)
        return await self.dispatch(match, request, ctx)

    async def dispatch(
        self,
        match: Match,
        request: UtteranceRequest,
        ctx: IntentContext,
        matched_by: MatchedBy = "rules",
    ) -> Action:
        """Run ``match.intent``'s handler. Raises KeyError for an unknown intent."""
        intent = self.registry.get(match.intent)
        if intent is None:
            raise KeyError(match.intent)
        reply = intent.handler(match, request, ctx)
        if inspect.isawaitable(reply):
            reply = await reply
        return Action(
            say=reply.say,
            view=reply.view,
            commands=list(reply.commands),
            intent=intent.name,
            matched_by=matched_by,
        )


UNMATCHED_SAY = "Sorry, I can't do that yet."


async def no_fallback(no_match: NoMatch, request: UtteranceRequest, ctx: IntentContext) -> Action:
    """Default when rules don't match and no fallback (Qwen) is wired."""
    return Action(say=UNMATCHED_SAY, view=None, commands=[], intent="none", matched_by="none")
