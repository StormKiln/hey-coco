import re

import pytest

from coco_hub.intents.durations import DURATION, llm_duration_slot, parse_duration
from coco_hub.intents.normalize import normalize


def test_llm_duration_slot_hands_seconds_to_parse_duration():
    slot = llm_duration_slot()
    assert (slot.type, slot.required, slot.minimum, slot.maximum) == ("integer", True, 1, 86400)
    key, text = slot.handler_slot("duration_s", 5400)
    assert (key, text) == ("duration", "5400 seconds")
    assert parse_duration(text) == 5400


DURATION_CASES = [
    ("10 minutes", 600),
    ("ten minutes", 600),
    ("1 minute", 60),
    ("a minute", 60),
    ("an hour", 3600),
    ("a second", 1),
    ("90 seconds", 90),
    ("ninety seconds", 90),
    ("5 min", 300),
    ("5 mins", 300),
    ("30 secs", 30),
    ("45 sec", 45),
    ("1 hr", 3600),
    ("2 hrs 30 mins", 9000),
    ("2 hours", 7200),
    ("1 hour 15 minutes", 4500),
    ("an hour and fifteen minutes", 4500),
    ("1 hour, 15 minutes, and 30 seconds", 4530),
    ("an hour and a half", 5400),
    ("1 hour and a half", 5400),
    ("a minute and a half", 90),
    ("half an hour", 1800),
    ("a half hour", 1800),
    ("half a minute", 30),
    ("two and a half minutes", 150),
    ("1.5 hours", 5400),
    ("a quarter of an hour", 900),
    ("three quarters of an hour", 2700),
    ("an hour and a quarter", 4500),
    ("twenty five minutes", 1500),
    ("one hundred and twenty seconds", 120),
    ("a couple of minutes", 120),
    ("Ten Minutes!", 600),
]

NOT_DURATIONS = [
    "",
    "minutes",
    "ten",
    "pasta",
    "10 bananas",
    "10 minutes ago",
    "1 minute 1 minute",
    "for 10 minutes",
]


@pytest.mark.parametrize(("text", "seconds"), DURATION_CASES)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", NOT_DURATIONS)
def test_not_a_duration(text):
    assert parse_duration(text) is None


def test_duration_table_is_big_enough():
    assert len(DURATION_CASES) >= 30


@pytest.mark.parametrize(("text", "seconds"), DURATION_CASES)
def test_duration_pattern_embeds_in_intent_patterns(text, seconds):
    pattern = re.compile(rf"set a timer for (?P<duration>{DURATION})")
    m = pattern.fullmatch(f"set a timer for {normalize(text)}")
    assert m is not None
    assert parse_duration(m["duration"]) == seconds
