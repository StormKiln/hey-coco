"""Device intents (#134): "turn on / off <device>", "is <device> on", "dim <device>".

Patterns are generated from the device names the ``DeviceService`` knows (config names,
aliases and device-reported aliases, normalised like utterances), recompiled whenever that
set changes, so nothing here knows any brand or any household's devices.

Rules only: these intents have no ``description``, so the Qwen fallback never sees them and a
misheard request can't switch anything. A lower-priority catch-all answers "I don't know a
device called X" for the explicit "turn on/off ..." forms, except for Coco's own features
(music, timers, alarms, ...), which keep going where they went before.

Replies confirm only what the device reported when read back.
"""

import re
from collections.abc import Callable, Iterable, Mapping

from coco_hub.devices.errors import (
    AuthRequired,
    DeviceError,
    UnknownDevice,
    Unreachable,
    Unsupported,
)
from coco_hub.devices.model import Capability, DeviceState, PowerState
from coco_hub.intents.engine import Intent, IntentContext, IntentRegistry, Match, Reply
from coco_hub.protocol import AnswerView, UtteranceRequest

NameSource = Callable[[], Mapping[str, str]]
"""Normalised spoken name -> the name Coco says back (``DeviceService.spoken_names``)."""

_D = "{D}"
_THE = r"(?:the |my )?"
_STATE = r"(?P<state>on|off)"

POWER_TEMPLATES = (
    rf"turn {_STATE} {_THE}{_D}",
    rf"turn {_THE}{_D} {_STATE}",
    rf"switch {_STATE} {_THE}{_D}",
    rf"switch {_THE}{_D} {_STATE}",
    rf"{_THE}{_D} {_STATE}",
)
STATE_TEMPLATES = (
    rf"is {_THE}{_D} (?:still )?(?:turned |switched )?(?:on|off)"
    r"(?: or (?:on|off))?(?: right now| now)?",
)
BRIGHTNESS_TEMPLATES = (
    rf"(?:dim|brighten|darken) {_THE}{_D}(?: (?:up|down|a bit|a little))?",
    rf"turn (?:up|down) {_THE}{_D}",
    rf"turn {_THE}{_D} (?:up|down)",
    rf"(?:set|put) {_THE}{_D}(?: brightness)? to \d+(?:\.\d+)?(?: percent)?",
)

# The catch-all: any 1-4 word name. Words that are Coco's own features (or pronouns) are left
# to the intents and fallback that handled them before devices existed.
_ANY = r"(?P<device>[a-z0-9]+(?: [a-z0-9]+){0,3})"
UNKNOWN_TEMPLATES = (
    *(t.replace(_D, _ANY) for t in POWER_TEMPLATES[:4]),
    *(t.replace(_D, _ANY) for t in STATE_TEMPLATES),
)
NOT_DEVICES = frozenset(
    """it that this them everything something music song songs radio podcast playlist sound
    sounds noise noises white alarm alarms timer timers clock picture pictures photo photos
    video videos movie volume screen display coco tune tunes""".split()
)


class NameMatcher:
    """A matcher callable for the engine: the templates with ``{D}`` = the current names."""

    def __init__(self, names: NameSource, templates: Iterable[str]) -> None:
        self._names = names
        self._templates = tuple(templates)
        self._key: tuple[str, ...] | None = None
        self._compiled: tuple[re.Pattern[str], ...] = ()

    def _patterns(self) -> tuple[re.Pattern[str], ...]:
        key = tuple(sorted(self._names(), key=lambda n: (-len(n), n)))
        if key != self._key:
            self._key = key
            if key:
                alt = "(?P<device>" + "|".join(re.escape(n) for n in key) + ")"
                self._compiled = tuple(re.compile(t.replace(_D, alt)) for t in self._templates)
            else:
                self._compiled = ()
        return self._compiled

    def __call__(self, text: str) -> dict[str, str] | None:
        for pattern in self._patterns():
            m = pattern.fullmatch(text)
            if m is not None:
                return {k: v for k, v in m.groupdict().items() if v is not None}
        return None


def _unknown_matcher(names: NameSource) -> Callable[[str], dict[str, str] | None]:
    patterns = tuple(re.compile(t) for t in UNKNOWN_TEMPLATES)

    def match(text: str) -> dict[str, str] | None:
        for pattern in patterns:
            m = pattern.fullmatch(text)
            if m is None:
                continue
            device = m.group("device")
            if NOT_DEVICES.intersection(device.split()) or device in names():
                return None
            return {k: v for k, v in m.groupdict().items() if v is not None}
        return None

    return match


# --- replies -----------------------------------------------------------------------------


def _answer(text: str) -> Reply:
    return Reply(say=text, view=AnswerView(text=text))


def _the(name: str) -> str:
    """ "the desk lamp", but "Sam's lamp" and "the fan" as they are."""
    first = name.split()[0].lower() if name.split() else ""
    if first in ("the", "my") or first.endswith(("'s", "s'", "’s")):
        return name
    return f"the {name}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _describe(name: str, state: DeviceState) -> str:
    if state.power is PowerState.MIXED:
        parts = ", ".join(f"{o.name} is {'on' if o.on else 'off'}" for o in state.outlets)
        return f"{_cap(name)} is partly on: {parts}."
    return f"{_cap(name)} is {state.power.value}."


def _explain(exc: DeviceError, name: str) -> str:
    if isinstance(exc, Unreachable):
        return f"I couldn't reach {_the(name)}. It may be unplugged or off the network."
    if isinstance(exc, AuthRequired):
        return (
            f"{_cap(_the(name))} needs a login before I can control it. See the Hub's device setup."
        )
    if isinstance(exc, UnknownDevice):
        return f"I couldn't find {_the(name)}. Check its name in the Hub's device setup."
    if isinstance(exc, Unsupported):
        return f"{_cap(_the(name))} can't do that."
    return f"Something went wrong talking to {_the(name)}."


def _service(ctx: IntentContext):
    return ctx.services.get("devices")


def _display(ctx: IntentContext, spoken: str) -> str:
    svc = _service(ctx)
    try:
        return svc.display_name(spoken) if svc is not None else spoken
    except DeviceError:
        return spoken


async def device_power(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    spoken, on = match.slots["device"], match.slots["state"] == "on"
    name = _display(ctx, spoken)
    svc = _service(ctx)
    try:
        if svc is None:
            raise UnknownDevice("no device service")
        if Capability.POWER not in await svc.capabilities(spoken):
            return _answer(f"{_cap(_the(name))} can't be switched on or off.")
        change = await svc.set_power(spoken, on)
    except DeviceError as exc:
        return _answer(_explain(exc, name))
    word = "on" if on else "off"
    if not change.confirmed:
        now = "partly on" if change.after.power is PowerState.MIXED else change.after.power.value
        return _answer(f"I asked {_the(name)} to turn {word}, but it still says it's {now}.")
    if not change.changed:
        return _answer(f"{_cap(name)} was already {word}.")
    return _answer(f"{_cap(name)} is {word}.")


async def device_state(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    spoken = match.slots["device"]
    name = _display(ctx, spoken)
    svc = _service(ctx)
    try:
        if svc is None:
            raise UnknownDevice("no device service")
        state = await svc.state(spoken)
    except DeviceError as exc:
        return _answer(_explain(exc, name))
    return _answer(_describe(name, state))


async def device_brightness(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    spoken = match.slots["device"]
    name = _display(ctx, spoken)
    svc = _service(ctx)
    try:
        if svc is None:
            raise UnknownDevice("no device service")
        capabilities = await svc.capabilities(spoken)
    except DeviceError as exc:
        return _answer(_explain(exc, name))
    if Capability.BRIGHTNESS not in capabilities:
        return _answer(f"{_cap(_the(name))} can't dim.")
    # No backend implements brightness yet; say so rather than pretend.
    return _answer(f"I can't change how bright {_the(name)} is yet.")


def device_unknown(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    svc = _service(ctx)
    if svc is None or not svc.spoken_names():
        return _answer("No devices are set up yet.")
    return _answer(f"I don't know a device called {match.slots['device']}.")


def _no_devices() -> Mapping[str, str]:
    return {}


def register(registry: IntentRegistry, names: NameSource | None = None) -> None:
    names = names or _no_devices
    # No ``description``: never offered to the Qwen fallback (see module docstring).
    registry.register(
        Intent("device.power", patterns=[NameMatcher(names, POWER_TEMPLATES)], handler=device_power)
    )
    registry.register(
        Intent("device.state", patterns=[NameMatcher(names, STATE_TEMPLATES)], handler=device_state)
    )
    registry.register(
        Intent(
            "device.brightness",
            patterns=[NameMatcher(names, BRIGHTNESS_TEMPLATES)],
            handler=device_brightness,
        )
    )
    registry.register(
        Intent(
            "device.unknown",
            patterns=[_unknown_matcher(names)],
            handler=device_unknown,
            priority=-1,  # any other intent that matches wins
        )
    )
