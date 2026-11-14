"""Qwen as the rules engine's fallback (``app.state.fallback``).

On ``NoMatch`` the transcript goes to Qwen with the registry's closed set of intents
(``Catalog``). The reply is validated; a malformed reply gets one repair request. Then:

- a registered intent -> its handler, via ``RulesEngine.dispatch(..., matched_by="qwen")``;
- ``general.answer`` -> the answer, tidied to at most three plain spoken sentences;
- ``none`` -> "Sorry, I can't do that yet.";
- still invalid -> answered as a general question if it looks like one, else "sorry".

The whole thing has a hard deadline (``qwen.timeout_s``, 6 s); past it the Hub says "Sorry,
that took too long." If Ollama is down the Hub behaves as if there were no fallback.
"""

import asyncio
import logging
import re
import time
import unicodedata
from typing import Any

from coco_hub.intents.engine import (
    UNMATCHED_SAY,
    IntentContext,
    Match,
    NoMatch,
    RulesEngine,
    no_fallback,
)
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import Action, AnswerView, UtteranceRequest
from coco_hub.qwen.catalog import GENERAL, NONE, Catalog, Interpretation, InvalidOutput
from coco_hub.qwen.client import OllamaClient, OllamaError
from coco_hub.qwen.safety import SAFETY_RULE, fixed_safety_answer, pet_food_answer

log = logging.getLogger(__name__)

TOO_SLOW_SAY = "Sorry, that took too long."
NOT_SURE_SAY = "I'm not sure about that."
MAX_SENTENCES = 3
WARM_UP_TIMEOUT_S = 120.0
WARM_UP_UTTERANCE = "what time is it"

REPAIR_REQUEST = (
    "That reply was not valid ({error}). Reply again with ONLY one JSON object that follows "
    "the output rules and uses an intent from the list."
)
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
ANSWER_PROMPT = (
    "You are Coco, a voice assistant on a family kitchen iPad. Kids and adults talk to you. "
    'Answer the question below. Reply with ONLY {"answer": "<text>"}: one or two short '
    "sentences, plain words a child understands, no lists, no markdown, no emoji. If you are "
    "not sure of a fact, say so briefly.\n" + SAFETY_RULE
)

_QUESTION_WORDS = frozenset(
    "what who whom whose when where why how which is are was were do does did can could "
    "will would should shall may might have has am explain".split()
)


def looks_like_question(raw: str) -> bool:
    if raw.strip().endswith("?"):
        return True
    words = normalize(raw).split()
    return bool(words) and (words[0] in _QUESTION_WORDS or words[:2] == ["tell", "me"])


_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_LINE_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|[-*•+]\s+|\d+[.)]\s+|>\s*)", re.MULTILINE)
_MD_MARKS = re.compile(r"(\*\*|__|\*|`+|~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9])")


def _is_emoji(ch: str) -> bool:
    return (
        unicodedata.category(ch) in ("So", "Cs")
        or ch in ("‍", "️", "︎")
        or 0x1F000 <= ord(ch) <= 0x1FAFF
    )


def tidy_answer(text: str, max_sentences: int = MAX_SENTENCES) -> str:
    """Plain spoken text: no markdown or emoji, one line, at most ``max_sentences``."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_LINE_PREFIX.sub("", text)
    text = _MD_MARKS.sub("", text)
    text = "".join(ch for ch in text if not _is_emoji(ch))
    text = " ".join(text.split())
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)  # "happy 😺." -> "happy ."
    if not text:
        return ""
    sentences = _SENTENCE_END.split(text)
    text = " ".join(s.strip() for s in sentences[:max_sentences] if s.strip())
    if text[-1] not in ".!?":
        text += "."
    return text


def _say(text: str, intent: str, matched_by: str) -> Action:
    return Action(
        say=text,
        view=AnswerView(text=text) if intent == GENERAL else None,
        commands=[],
        intent=intent,
        matched_by=matched_by,  # type: ignore[arg-type]
    )


class QwenFallback:
    """``async (NoMatch, UtteranceRequest, IntentContext) -> Action`` backed by Ollama."""

    def __init__(
        self,
        client: OllamaClient,
        engine: RulesEngine,
        *,
        timeout_s: float = 6.0,
        log_transcripts: bool = True,
    ) -> None:
        self.client = client
        self.log_transcripts = log_transcripts
        self.engine = engine
        self.timeout_s = timeout_s
        self._catalog: Catalog | None = None
        self._catalog_size = -1
        self._warming: asyncio.Task[None] | None = None

    def _t(self, text: str) -> str:
        """Household speech (and model replies derived from it) only reach the log when
        ``diagnostics.log_transcripts`` is on."""
        return text if self.log_transcripts else "<transcript hidden>"

    @property
    def catalog(self) -> Catalog:
        """Built from the registry on first use, rebuilt if intents were added since."""
        registry = self.engine.registry
        if self._catalog is None or self._catalog_size != len(registry):
            self._catalog = Catalog.from_registry(registry)
            self._catalog_size = len(registry)
        return self._catalog

    async def __call__(
        self, no_match: NoMatch, request: UtteranceRequest, ctx: IntentContext
    ) -> Action:
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.timeout_s):
                result = await self.interpret(no_match, request)
        except TimeoutError:
            log.warning("qwen: gave up on %r after %.1fs", self._t(no_match.raw), self.timeout_s)
            self.schedule_warm_up()  # a timeout is usually a cold model
            return _say(TOO_SLOW_SAY, NONE, "none")
        except OllamaError as exc:
            log.warning("qwen: unavailable (%s); no fallback for %r", exc, self._t(no_match.raw))
            return await no_fallback(no_match, request, ctx)
        log.info(
            "qwen: %r -> %s %s in %.2fs",
            self._t(no_match.raw),
            result.intent if result else "invalid",
            dict(result.slots) if result else "",
            time.monotonic() - started,
        )
        if result is None:
            return _say(UNMATCHED_SAY, NONE, "none")
        return await self.act(result, no_match, request, ctx)

    async def interpret(
        self, no_match: NoMatch, request: UtteranceRequest
    ) -> Interpretation | None:
        """What Qwen makes of the utterance, or None if it never gave a usable reply.

        No deadline of its own (the caller sets one). Raises ``OllamaError`` if Ollama is
        down or broken. Common pet-poison questions are answered from a fixed table without
        the model (``safety.pet_food_answer``).
        """
        utterance = request.text.strip() or no_match.raw or no_match.text
        if (safe := pet_food_answer(utterance)) is not None:
            log.info("qwen: %r answered from the pet-food table", self._t(utterance))
            return Interpretation(GENERAL, answer=safe)
        if (safe := fixed_safety_answer(utterance)) is not None:
            log.info(
                "qwen: %r answered with the fixed allergy/medicine referral", self._t(utterance)
            )
            return Interpretation(GENERAL, answer=safe)
        catalog = self.catalog
        reply = await self.client.chat_json(
            catalog.system_prompt, utterance, catalog.schema, self.timeout_s
        )
        try:
            return catalog.validate(reply)
        except InvalidOutput as exc:
            log.info("qwen: invalid reply %r (%s); asking for a repair", self._t(reply[:200]), exc)
            history = [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": REPAIR_REQUEST.format(error=exc)},
            ]
        reply = await self.client.chat_json(
            catalog.system_prompt, utterance, catalog.schema, self.timeout_s, history=history
        )
        try:
            return catalog.validate(reply)
        except InvalidOutput as exc:
            log.warning("qwen: invalid reply after repair %r (%s)", self._t(reply[:200]), exc)
        if not looks_like_question(utterance):
            return None
        return await self._answer_only(utterance)

    async def _answer_only(self, utterance: str) -> Interpretation | None:
        reply = await self.client.chat_json(ANSWER_PROMPT, utterance, ANSWER_SCHEMA, self.timeout_s)
        try:
            return Catalog.validate_answer(reply)
        except InvalidOutput as exc:
            log.warning("qwen: no usable answer %r (%s)", self._t(reply[:200]), exc)
            return None

    async def act(
        self,
        result: Interpretation,
        no_match: NoMatch,
        request: UtteranceRequest,
        ctx: IntentContext,
    ) -> Action:
        if result.intent == GENERAL:
            return _say(tidy_answer(result.answer or "") or NOT_SURE_SAY, GENERAL, "qwen")
        intent = self.catalog.intents.get(result.intent)
        if intent is None:  # "none"
            return _say(UNMATCHED_SAY, NONE, "qwen")
        if intent.applies is not None and not intent.applies(request.context):
            log.info("qwen: %s does not apply in this context", intent.name)
            return _say(UNMATCHED_SAY, NONE, "qwen")
        converted = (
            intent.llm_slots[key].handler_slot(key, value) for key, value in result.slots.items()
        )
        slots = {key: text for key, text in converted if text}
        match = Match(intent=intent.name, slots=slots, text=no_match.text)
        return await self.engine.dispatch(match, request, ctx, matched_by="qwen")

    async def warm_up(self) -> None:
        """Load the model and prime the prompt cache. Logs failures; never raises."""
        health = await self.client.health()
        if not health.reachable:
            log.warning("qwen: Ollama not reachable (%s); fallback off until it is", health.error)
            return
        if not health.model_present:
            log.warning("qwen: model %s not in Ollama; run `ollama pull`", self.client.model)
            return
        catalog = self.catalog
        started = time.monotonic()
        try:
            reply = await self.client.chat_json(
                catalog.system_prompt, WARM_UP_UTTERANCE, catalog.schema, WARM_UP_TIMEOUT_S
            )
        except (OllamaError, TimeoutError) as exc:
            log.warning("qwen: warm-up failed: %s", exc or type(exc).__name__)
            return
        log.info(
            "qwen: %s warm in %.1fs (%s)", self.client.model, time.monotonic() - started, reply
        )

    def schedule_warm_up(self) -> asyncio.Task[None] | None:
        """Warm up in the background unless a warm-up is already running."""
        if self._warming is not None and not self._warming.done():
            return None
        self._warming = asyncio.create_task(self.warm_up(), name="qwen-warm-up")
        return self._warming

    async def aclose(self) -> None:
        if self._warming is not None and not self._warming.done():
            self._warming.cancel()
            try:
                await self._warming
            except asyncio.CancelledError:
                pass
        await self.client.aclose()
