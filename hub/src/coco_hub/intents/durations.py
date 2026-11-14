"""Spoken durations → seconds.

Embed ``DURATION`` in an intent pattern to capture a duration slot, then convert it::

    Intent("timer.create", patterns=[rf"set a timer for (?P<duration>{DURATION})"], ...)
    seconds = parse_duration(match.slots["duration"])

``DURATION`` matches *normalised* text (``1.5 hour``, ``1 hour 15 minutes``) and contains
no capturing groups. ``parse_duration`` normalises its input first, so it also accepts raw
phrases like ``"an hour and a half"``.
"""

import re

from coco_hub.intents.normalize import normalize

_UNIT_SECONDS = {
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "secs": 1,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "mins": 60,
    "hour": 3600,
    "hours": 3600,
    "hr": 3600,
    "hrs": 3600,
}
_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)"
_PART = rf"{_NUM} {_UNIT}"

DURATION = rf"{_PART}(?:(?: and)? {_PART})*"
"""Regex source (no capturing groups) matching a normalised duration phrase."""

_DURATION_RE = re.compile(DURATION)
_PART_RE = re.compile(rf"({_NUM}) ({_UNIT})")


def parse_duration(text: str) -> int | None:
    """Seconds in a spoken duration, or ``None`` if the whole text isn't one duration.

    Each unit may appear once (``1 minute 1 minute`` is rejected). Rounds to whole seconds.
    """
    normalized = normalize(text)
    if not _DURATION_RE.fullmatch(normalized):
        return None
    total = 0.0
    seen: set[int] = set()
    for number, unit in _PART_RE.findall(normalized):
        scale = _UNIT_SECONDS[unit]
        if scale in seen:
            return None
        seen.add(scale)
        total += float(number) * scale
    return int(total + 0.5)
