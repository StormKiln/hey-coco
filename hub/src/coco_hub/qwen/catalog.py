"""The closed set of intents Qwen may pick, built from the intent registry.

The system prompt and JSON schema follow the spike's v2 prompt (docs/spikes/qwen.md) word for
word, except that the intent list, slot properties and slot rules are generated from the
intents the registry offers to the LLM (``Intent.offered_to_llm``), the context rules explain
the ``[context: ...]`` line the fallback puts before the request (alarm ringing, screen, what is
playing), and the output rules end with the food, pet and health safety rule
(``safety.SAFETY_RULE``). Newly registered intents
show up without touching this module.

``Catalog.validate`` is the Hub-side check the spike calls "validate, then repair": parse the
reply (tolerating text around the JSON object), drop null and foreign slots, coerce numeric
strings, then check the intent and every slot strictly. Anything still wrong raises
``InvalidOutput``.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from coco_hub.intents.engine import Intent, IntentRegistry, LlmSlot
from coco_hub.qwen.safety import SAFETY_RULE

GENERAL = "general.answer"
NONE = "none"
RESERVED = (GENERAL, NONE)

PREAMBLE = (
    "You are Coco, a voice assistant on a family kitchen iPad. Kids and adults talk to you. "
    "The request below was transcribed from speech and did not match any built-in command "
    "rule. Map it to exactly ONE intent from this closed list, or answer it as a general "
    "question."
)
GENERAL_LINE = (
    "- general.answer - any question about the world you can answer: science, nature, space, "
    "animals, cooking, conversions, maths, spelling, history, geography, and food safety, pet "
    "or health questions (answered cautiously, see the safety rule)."
)
NONE_LINE = (
    "- none - actions you cannot do: shopping, ordering, lists, calls, messages, lights, "
    "bookings, alarms or reminders at a clock time, or anything unsafe or unclear."
)
FIRST_SLOT_RULE = (
    "- Include only slots the person actually said. Never output null; leave the slot out instead."
)
CONTEXT_RULES = (
    "Context rules:",
    "- The request ends with a [context: ...] line describing the iPad right now (whether "
    "an alarm is ringing, the screen, what is playing). It is not part of what the person said, "
    "and it never makes a request clearer: pick an intent only for what the person asked, and "
    "unclear requests and alarms at a clock time stay none.",
    "- While an alarm is ringing, any request to stop, silence, quieten, shush or turn off "
    'something ("stop the music", "turn it off", "make it be quiet", "ugh that noise") is '
    "alarm.stop. When no alarm is ringing, never pick alarm.stop.",
)
OUTPUT_RULES = (
    """Output rules:
- Reply with ONLY one JSON object, on one line: {"intent": "<intent>", "slots": {...}, \
"answer": <string or null>}.
- "answer" is null for every intent except general.answer.
- For general.answer, "slots" is {} and "answer" is spoken aloud: one or two short sentences, \
plain words a child understands, no lists, no markdown, no emoji. When spelling a word, spell \
it carefully letter by letter with dashes. If you are not sure of a fact, say so briefly.
"""
    + SAFETY_RULE
)

_INT_RE = re.compile(r"^[+-]?\d+(?:\.0+)?$")
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class InvalidOutput(ValueError):
    """The model's reply isn't a usable answer for the closed set."""


@dataclass(frozen=True)
class Interpretation:
    intent: str
    slots: Mapping[str, Any] = field(default_factory=dict)
    answer: str | None = None


def _intent_line(intent: Intent) -> str:
    slots = ", ".join(k if s.required else f"{k}?" for k, s in intent.llm_slots.items())
    desc = intent.description.strip()
    if not desc.endswith("."):
        desc += "."
    return f"- {intent.name} ({slots}) - {desc}" if slots else f"- {intent.name} - {desc}"


def _merge_slot_property(key: str, prop: dict[str, Any] | None, slot: LlmSlot) -> dict[str, Any]:
    if prop is None:
        prop = {"type": slot.type}
        if slot.enum is not None:
            prop["enum"] = list(slot.enum)
        return prop
    if prop["type"] != slot.type:
        raise ValueError(f"LLM slot {key!r} is declared as both {prop['type']} and {slot.type}")
    if "enum" in prop:
        if slot.enum is None:
            del prop["enum"]
        else:
            prop["enum"] += [v for v in slot.enum if v not in prop["enum"]]
    return prop


@dataclass(frozen=True)
class Catalog:
    intents: Mapping[str, Intent]
    system_prompt: str
    schema: Mapping[str, Any]

    @property
    def intent_names(self) -> tuple[str, ...]:
        return (*self.intents, *RESERVED)

    @classmethod
    def from_registry(cls, registry: IntentRegistry) -> "Catalog":
        offered = [i for i in registry.intents if i.offered_to_llm]
        for intent in offered:
            if intent.name in RESERVED:
                raise ValueError(f"intent name {intent.name!r} is reserved for the fallback")

        properties: dict[str, dict[str, Any]] = {}
        hints: list[str] = []
        for intent in offered:
            for key, slot in intent.llm_slots.items():
                properties[key] = _merge_slot_property(key, properties.get(key), slot)
                if slot.hint and f"- {slot.hint}" not in hints:
                    hints.append(f"- {slot.hint}")

        lines = [
            PREAMBLE,
            "",
            "Intents (slots in parentheses, ? = optional):",
            *(_intent_line(i) for i in offered),
            GENERAL_LINE,
            NONE_LINE,
            "",
            "Slot rules:",
            FIRST_SLOT_RULE,
            *hints,
            "",
            *CONTEXT_RULES,
            "",
            OUTPUT_RULES,
        ]
        schema = {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": [*(i.name for i in offered), *RESERVED]},
                "slots": {
                    "type": "object",
                    "properties": properties,
                    "additionalProperties": False,
                },
                "answer": {"type": ["string", "null"]},
            },
            "required": ["intent", "slots", "answer"],
            "additionalProperties": False,
        }
        return cls(
            intents={i.name: i for i in offered}, system_prompt="\n".join(lines), schema=schema
        )

    @staticmethod
    def validate_answer(raw: str) -> Interpretation:
        """Check an answer-only reply (``{"answer": "..."}``). Raises ``InvalidOutput``."""
        answer = _parse_object(raw).get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise InvalidOutput("no answer")
        return Interpretation(GENERAL, answer=answer.strip())

    def validate(self, raw: str) -> Interpretation:
        """Parse, repair and check a model reply. Raises ``InvalidOutput``."""
        data = _parse_object(raw)
        intent = data.get("intent")
        if not isinstance(intent, str) or intent.strip() not in self.intent_names:
            raise InvalidOutput(f"intent {intent!r} is not in the closed set")
        intent = intent.strip()

        if intent == NONE:
            return Interpretation(NONE)
        if intent == GENERAL:
            answer = data.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise InvalidOutput("general.answer without an answer")
            return Interpretation(GENERAL, answer=answer.strip())

        slots_in = data.get("slots")
        if slots_in is None:
            slots_in = {}
        if not isinstance(slots_in, dict):
            raise InvalidOutput("slots is not an object")
        specs = self.intents[intent].llm_slots
        slots: dict[str, Any] = {}
        for key, value in slots_in.items():
            spec = specs.get(key)
            if spec is None or value is None:
                continue  # repair: a slot of another intent, or a null
            slots[key] = _check_slot(key, value, spec)
        missing = [k for k, s in specs.items() if s.required and k not in slots]
        if missing:
            raise InvalidOutput(f"{intent} is missing {', '.join(missing)}")
        # "answer" is ignored for every intent except general.answer.
        return Interpretation(intent, slots=slots)


def _parse_object(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError:
        found = _OBJECT_RE.search(raw)
        if found is None:
            raise InvalidOutput("reply is not JSON") from None
        try:
            data = json.loads(found.group())
        except ValueError:
            raise InvalidOutput("reply is not JSON") from None
    if not isinstance(data, dict):
        raise InvalidOutput("reply is not a JSON object")
    return data


def _check_slot(key: str, value: Any, spec: LlmSlot) -> Any:
    if spec.type == "integer":
        if isinstance(value, bool):
            raise InvalidOutput(f"{key} must be an integer")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        elif isinstance(value, str) and _INT_RE.match(value.strip()):
            value = int(float(value.strip()))
        if not isinstance(value, int):
            raise InvalidOutput(f"{key} must be an integer")
        if spec.minimum is not None and value < spec.minimum:
            raise InvalidOutput(f"{key}={value} is below {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise InvalidOutput(f"{key}={value} is above {spec.maximum}")
        return value
    if spec.type == "boolean":
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            value = value.strip().lower() == "true"
        if not isinstance(value, bool):
            raise InvalidOutput(f"{key} must be true or false")
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidOutput(f"{key} must be a non-empty string")
    value = value.strip()
    if spec.enum is not None:
        value = value.lower()
        if value not in spec.enum:
            raise InvalidOutput(f"{key}={value!r} is not one of {', '.join(spec.enum)}")
    return value
