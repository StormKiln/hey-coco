"""Turning numbers and times into words Coco can say."""

import math
from datetime import datetime, timedelta


def round_temp(value: float) -> int:
    """Round half away from zero (``4.5`` → 5, ``-0.5`` → -1); never ``-0``."""
    rounded = int(math.copysign(math.floor(abs(value) + 0.5), value))
    return rounded or 0


def spoken_temp(value: float) -> str:
    """``3``, ``minus 1``, ``0``: no unit; negatives say "minus"."""
    n = round_temp(value)
    return f"minus {-n}" if n < 0 else str(n)


def degrees(value: float) -> str:
    """``3 degrees``, ``minus 1 degree``."""
    n = round_temp(value)
    return f"{spoken_temp(n)} {'degree' if abs(n) == 1 else 'degrees'}"


def a_or_an(n: int) -> str:
    """The article before a spoken number: "an 80 percent chance", "a 70 percent chance"."""
    return "an" if str(n).startswith("8") or n in (11, 18) else "a"


def spoken_hour(dt: datetime) -> str:
    """``9 AM``, ``2 PM``, ``noon``, ``midnight``."""
    if dt.hour == 0:
        return "midnight"
    if dt.hour == 12:
        return "noon"
    return f"{dt.hour % 12} {'AM' if dt.hour < 12 else 'PM'}"


def _part(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def part_of_day(dt: datetime, now: datetime) -> str:
    """When ``dt`` is, relative to ``now`` (both Hub-local): ``this afternoon``,
    ``tonight``, ``overnight``, ``tomorrow morning``…"""
    day = (dt.date() - now.date()).days
    part = _part(dt.hour)
    if day == 0:
        if part == "night":
            return "tonight" if dt.hour >= 21 else "overnight"
        return f"this {part}"
    if day == 1:
        if dt.hour < 5:
            return "overnight"
        return "tomorrow night" if part == "night" else f"tomorrow {part}"
    return f"{dt:%A} {'night' if part == 'night' else part}"


def floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def next_hour(dt: datetime) -> datetime:
    return floor_hour(dt) + timedelta(hours=1)
