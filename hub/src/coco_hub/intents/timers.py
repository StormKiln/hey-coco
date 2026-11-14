"""Timer intents: create, query, list, cancel, add time, and stopping a ringing alarm.

Handlers use the ``TimerService`` in ``ctx.services["timers"]``. Patterns match normalised
text (see ``normalize.py``), so numbers are digits and ``an hour and a half`` is
``1.5 hour``.

Which timer an unnamed request means: a ringing one if exactly one is ringing (for
cancel / add time), else the only active timer; with two or more, Coco asks which.
"""

import math
from datetime import datetime

from coco_hub.intents.durations import DURATION, parse_duration
from coco_hub.intents.engine import Intent, IntentContext, IntentRegistry, Match, Reply
from coco_hub.protocol import AlarmStop, Timer, TimersView, UtteranceRequest
from coco_hub.timers.service import (
    MAX_TIMER_S,
    TimerLimitError,
    TimerService,
    normalize_timer_name,
)

_D = DURATION
_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)"
# Lazy, so optional literal words after it ("timer", "one") are not swallowed.
_NAME = r"[a-z][a-z0-9]*(?: [a-z0-9]+){0,3}?"
# Short, lazy name for loose patterns ("cancel the pasta", "scrap the broccoli one").
_SHORT_NAME = r"[a-z][a-z0-9]*(?: [a-z0-9]+)??"
_ART = r"(?:(?:a|an|the|my|another|a new|new) )?"
_SET = r"(?:set|start|make|create|put on|put|give me|add|begin)(?: up| me)?"
_THE = r"(?:the |my )?"
_ON = rf"(?: (?:on|for) {_THE}(?P<name>{_NAME})(?: timer)?)?"
_TO = rf"(?: (?:to|on|onto|for) {_THE}(?P<name>{_NAME})(?: timer)?)?"

TIMERS = TimersView()


# --- speech helpers ----------------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _join(items: list[str], last: str = " and ") -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + last + items[-1]


def spoken_duration(seconds: int) -> str:
    """``5400`` → ``1 hour and 30 minutes``; ``90`` → ``1 minute and 30 seconds``."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [
        _plural(n, unit)
        for n, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second"))
        if n
    ]
    return _join(parts) or "0 seconds"


def spoken_remaining(timer: Timer, now: datetime) -> str:
    """``About 4 minutes`` / ``30 seconds`` / ``15 minutes`` (exact)."""
    seconds = max(0, math.ceil((timer.ends_at - now).total_seconds()))
    if seconds < 60:
        return _plural(seconds, "second")
    minutes = round(seconds / 60)
    text = spoken_duration(minutes * 60)
    return text if seconds % 60 == 0 else f"About {text}"


def label(name: str | None) -> str:
    """``pasta`` → ``pasta timer``; ``timer 2`` stays ``timer 2``."""
    if not name or name == "timer" or name.startswith("timer "):
        return name or "timer"
    return f"{name} timer"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _a(word: str) -> str:
    return f"an {word}" if word[:1] in "aeiou" else f"a {word}"


def _say(text: str, *commands: AlarmStop) -> Reply:
    return Reply(say=text, view=TIMERS, commands=tuple(commands))


def _service(ctx: IntentContext) -> TimerService:
    return ctx.services["timers"]


def _seconds(slots: dict[str, str]) -> int | None:
    if "duration" in slots:
        return parse_duration(slots["duration"])
    if "unit" in slots:
        return parse_duration(f"{slots.get('count', '1')} {slots['unit']}")
    return None


def _resolve(
    service: TimerService, name_slot: str | None, *, prefer_ringing: bool
) -> Timer | Reply:
    """The timer a request refers to, or a reply explaining why there isn't one."""
    name = normalize_timer_name(name_slot)
    if name is not None:
        found = service.find_by_name(name)
        return found or _say(f"You don't have {_a(label(name))}.")
    active = service.list_active()
    if not active:
        return _say("You don't have any timers.")
    if prefer_ringing:
        ringing = [t for t in active if t.state == "ringing"]
        if len(ringing) == 1:
            return ringing[0]
    if len(active) == 1:
        return active[0]
    return _say(f"Which timer? You have {_join([t.name or 'timer' for t in active])}.")


def _stop_alarm_if_quiet(service: TimerService) -> tuple[AlarmStop, ...]:
    """``alarm.stop`` when nothing is ringing any more (the Display silences everything)."""
    if any(t.state == "ringing" for t in service.list_active()):
        return ()
    return (AlarmStop(),)


# --- handlers ----------------------------------------------------------------------------


def create(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    seconds = _seconds(match.slots)
    if not seconds:
        return _say("How long should the timer be?")
    if seconds > MAX_TIMER_S:
        return _say("Timers can be up to 24 hours.")
    try:
        timer = _service(ctx).create(match.slots.get("name"), seconds)
    except TimerLimitError as exc:
        return _say(str(exc))
    return _say(f"{_cap(label(timer.name))} set for {spoken_duration(timer.duration_s)}.")


def query(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    found = _resolve(_service(ctx), match.slots.get("name"), prefer_ringing=False)
    if isinstance(found, Reply):
        return found
    if found.state == "ringing":
        return _say(f"The {label(found.name)} is done.")
    return _say(f"{spoken_remaining(found, ctx.clock())} left on the {label(found.name)}.")


def list_timers(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    active = _service(ctx).list_active()
    if not active:
        return _say("You don't have any timers.")
    now = ctx.clock()
    items = [
        f"{t.name or 'timer'}, done"
        if t.state == "ringing"
        else f"{t.name or 'timer'}, {spoken_remaining(t, now).replace('About', 'about')} left"
        for t in active
    ]
    listed = items[0] if len(items) == 1 else "; ".join(items[:-1]) + "; and " + items[-1]
    return _say(f"You have {_plural(len(active), 'timer')}: {listed}.")


def cancel(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    service = _service(ctx)
    if "all" in match.slots:
        cancelled = service.cancel_all()
        if not cancelled:
            return _say("You don't have any timers.")
        if len(cancelled) == 1:
            return _say(f"{_cap(label(cancelled[0].name))} cancelled.", AlarmStop())
        return _say(f"All {len(cancelled)} timers cancelled.", AlarmStop())
    found = _resolve(service, match.slots.get("name"), prefer_ringing=True)
    if isinstance(found, Reply):
        return found
    was_ringing = found.state == "ringing"
    service.cancel(found.id)
    commands = _stop_alarm_if_quiet(service) if was_ringing else ()
    return _say(f"{_cap(label(found.name))} cancelled.", *commands)


def add_time(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    seconds = _seconds(match.slots)
    if not seconds:
        return _say("How much time should I add?")
    service = _service(ctx)
    found = _resolve(service, match.slots.get("name"), prefer_ringing=True)
    if isinstance(found, Reply):
        return found
    was_ringing = found.state == "ringing"
    try:
        updated = service.add_time(found.id, seconds)
    except TimerLimitError as exc:
        return _say(str(exc))
    commands = _stop_alarm_if_quiet(service) if was_ringing else ()
    remaining = spoken_remaining(updated, ctx.clock())
    return _say(
        f"Added {spoken_duration(seconds)}. {remaining} left on the {label(updated.name)}.",
        *commands,
    )


def alarm_stop(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    _service(ctx).dismiss_ringing()
    return Reply(say=None, view=None, commands=(AlarmStop(),))


# --- registration ------------------------------------------------------------------------

_CANCEL = r"(?:cancel|stop|delete|remove|clear|kill|end|turn off|switch off|get rid of|scrap)"
_DROP = r"(?:cancel|delete|scrap|get rid of)"
_THANKS = r"(?: (?:thanks|thank you|coco|i got it|got it))*"


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "timer.create",
            patterns=[
                rf"(?:{_SET} )?{_ART}(?:(?P<name>{_NAME}) )?timer (?:for|of|on) (?P<duration>{_D})",
                rf"(?:{_SET} )?{_ART}timer (?P<duration>{_D})",
                rf"(?:{_SET} )?{_ART}(?P<duration>{_D}) (?:(?P<name>{_NAME}) )?timer",
                rf"(?:{_SET} )?{_ART}(?P<duration>{_D}) timer (?:for|called|named) "
                rf"{_THE}(?P<name>{_NAME})",
                rf"(?:{_SET} )?{_ART}timer (?:for|of) (?P<duration>{_D}) "
                rf"(?:called|named|for|on) {_THE}(?P<name>{_NAME})",
                rf"remind me (?:in|after) (?P<duration>{_D})",
                rf"remind me (?:in|after) (?P<duration>{_D}) (?:about|to check(?: on)?) "
                rf"{_THE}(?P<name>{_NAME})",
                rf"(?P<duration>{_D}) (?:on|for) {_THE}(?P<name>{_NAME})",
            ],
            handler=create,
            description="Start a countdown timer, optionally named",
        )
    )
    registry.register(
        Intent(
            "timer.query",
            patterns=[
                rf"how (?:long|much time|much longer|many minutes)"
                rf"(?: is| do i have| have i got| is there)?(?: left| remaining| to go)?{_ON}",
                rf"how (?:long|much longer) (?:until|till|before) {_THE}(?P<name>{_NAME}) "
                rf"(?:is |will be )?(?:done|ready|finished)",
                rf"how (?:long|much longer) (?:until|till|before) {_THE}(?:(?P<name>{_NAME}) )?"
                rf"timer (?:goes off|is done|rings|finishes)",
                rf"when (?:is|will) {_THE}(?P<name>{_NAME}) (?:be )?(?:done|ready|finished)",
                rf"when (?:is|will|does) {_THE}(?:(?P<name>{_NAME}) )?timer "
                rf"(?:go off|be done|done|finish|ring)",
                rf"(?:what is )?(?:the )?time (?:left|remaining){_ON}",
                rf"how is {_THE}(?P<name>{_NAME}) timer (?:doing|going)",
            ],
            handler=query,
            description="Say how long is left on a timer",
        )
    )
    registry.register(
        Intent(
            "timer.list",
            patterns=[
                r"what timers (?:do i have|are (?:there|running|set|on|going))"
                r"(?: running| set| going| on)?",
                r"(?:list|show)(?: me)? (?:all )?(?:the |my )?timers",
                r"(?:do i have|are there|have i got) (?:any )?timers(?: running| set| going| on)?",
                r"how many timers(?: do i have| are (?:there|running|set|on))?(?: running| set)?",
                r"(?:my |the |all )?timers",
            ],
            handler=list_timers,
            description="List the running timers",
        )
    )
    registry.register(
        Intent(
            "timer.cancel",
            patterns=[
                rf"{_CANCEL}(?: the| my)?(?: (?P<name>{_NAME}))? timer",
                rf"{_CANCEL} (?:the )?(?P<name>timer \d+)",
                rf"{_DROP} {_THE}(?P<name>{_SHORT_NAME})(?: one| 1)?",
                rf"{_CANCEL} (?P<all>all|every)(?: of)?(?: the| my)? timers?",
                rf"{_CANCEL} {_THE}(?P<all>timers)",
            ],
            handler=cancel,
            description="Cancel a timer by name, or all timers",
        )
    )
    registry.register(
        Intent(
            "timer.add_time",
            patterns=[
                rf"add (?:another )?(?P<duration>{_D})(?: more)?{_TO}",
                rf"add (?P<count>{_NUM}) more (?P<unit>{_UNIT}){_TO}",
                rf"add (?:another|an extra|1 more) (?P<unit>{_UNIT}){_TO}",
                rf"(?:give|put) {_THE}(?P<name>{_NAME}) (?:another )?"
                rf"(?P<count>{_NUM}) more (?P<unit>{_UNIT})",
                rf"(?:give|put) {_THE}(?P<name>{_NAME}) (?:another )?(?P<duration>{_D})",
                rf"put (?:another )?(?P<count>{_NUM}) more (?P<unit>{_UNIT}) on {_THE}"
                rf"(?P<name>{_NAME})",
                rf"put (?:another )?(?P<duration>{_D}) (?:more )?on {_THE}(?P<name>{_NAME})",
                rf"(?:another )?(?P<count>{_NUM}) more (?P<unit>{_UNIT}){_ON}",
                rf"(?:extend|increase) {_THE}(?:(?P<name>{_NAME}) )?timer by (?P<duration>{_D})",
            ],
            handler=add_time,
            description="Add time to a running or ringing timer",
        )
    )
    registry.register(
        Intent(
            "alarm.stop",
            patterns=[
                "",  # a bare "ok" / "okay" normalises to nothing
                r"(?:stop|dismiss|silence|cancel|turn off|shut off|switch off)"
                r"(?: it| that| this)?(?: the)?"
                r"(?: (?:alarm|alarms|timer|timers|ringing|beeping|noise|sound))?(?: now)?"
                + _THANKS,
                r"(?:turn|shut|switch) (?:it|that|the alarm|the timer) off" + _THANKS,
                r"(?:ok|okay|alright|all right|yes|yep|thanks|thank you|got it|i got it|"
                r"i heard|i hear you|i am coming|coming|enough|done|quiet|be quiet|shut up|"
                r"shush|hush)" + _THANKS,
            ],
            handler=alarm_stop,
            priority=10,
            applies=lambda c: c.alarm_ringing,
            description="Stop the ringing alarm (only while one is ringing)",
        )
    )
