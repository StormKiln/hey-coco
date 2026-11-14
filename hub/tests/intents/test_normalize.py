import pytest

from coco_hub.intents.normalize import normalize, strip_filler, words_to_numbers

NORMALIZE_CASES = [
    # wake phrase, punctuation, case
    ("Hey Coco, what time is it?", "what time is it"),
    ("hey cocoa what time is it", "what time is it"),
    ("OK Coco. Stop!", "stop"),
    ("Coco, stop!", "stop"),
    ("Hey Coco", ""),
    ("", ""),
    ("   PLEASE   stop  ", "stop"),
    # contractions
    ("What's the time?", "what is the time"),
    ("whats the date", "what is the date"),
    ("Uh, what's today's date?", "what is todays date"),
    ("It's 3:30.", "it is 3:30"),
    # filler and politeness
    ("Can you please set a timer for ten minutes", "set a timer for 10 minutes"),
    ("could you tell me what time it is", "tell me what time it is"),
    ("um, uh, what time is it", "what time is it"),
    ("do you know what day it is", "what day it is"),
    ("would you mind stopping the music please", "stopping the music"),
    # number words
    ("set a timer for twenty five minutes", "set a timer for 25 minutes"),
    ("twenty-five", "25"),
    ("ninety seconds", "90 seconds"),
    ("eleven nineteen", "11 19"),
    ("seventy seven", "77"),
    ("zero", "0"),
    ("one hundred and five", "105"),
    ("a hundred and twenty seconds", "120 seconds"),
    ("two thousand twenty six", "2026"),
    ("five ten", "5 10"),
    # a / an as a quantity only
    ("one minute", "1 minute"),
    ("an hour", "1 hour"),
    ("wait a second", "wait 1 second"),
    ("a couple of minutes", "2 minutes"),
    ("set a timer", "set a timer"),
    ("show me an apple", "show me an apple"),
    ("play the next one", "play the next one"),
    ("which one", "which one"),
    # fractions
    ("set a timer for a minute and a half", "set a timer for 1.5 minute"),
    ("an hour and a half", "1.5 hour"),
    ("half an hour", "0.5 hour"),
    ("half a minute", "0.5 minute"),
    ("a half hour", "0.5 hour"),
    ("two and a half minutes", "2.5 minutes"),
    ("a quarter of an hour", "0.25 hour"),
    ("three quarters of an hour", "0.75 hour"),
    ("an hour and a quarter", "1.25 hour"),
    ("half past three", "half past 3"),
    # digits survive
    ("1.5 hours", "1.5 hours"),
    ("1,000 seconds", "1000 seconds"),
    ("volume to 50%", "volume to 50 percent"),
]


@pytest.mark.parametrize(("raw", "expected"), NORMALIZE_CASES)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), NORMALIZE_CASES)
def test_normalize_is_idempotent(raw, expected):
    assert normalize(expected) == expected


def test_normalize_has_plenty_of_cases():
    assert len(NORMALIZE_CASES) >= 30


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["ten"], ["10"]),
        (["twenty", "one"], ["21"]),
        (["one", "two"], ["1", "2"]),
        (["a", "minute"], ["1", "minute"]),
        (["a", "timer"], ["a", "timer"]),
        (["the", "last", "one"], ["the", "last", "one"]),
        (["nine", "hundred", "ninety", "nine"], ["999"]),
        (["a", "thousand"], ["1000"]),
        (["fish", "and", "chips"], ["fish", "and", "chips"]),
        (["5", "and", "twenty"], ["5", "and", "20"]),
    ],
)
def test_words_to_numbers(words, expected):
    assert words_to_numbers(words) == expected


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["hey", "coco", "stop"], ["stop"]),
        (["can", "you", "could", "you", "stop"], ["stop"]),
        (["stop", "please"], ["stop"]),
        (["play", "hey", "jude"], ["play", "hey", "jude"]),
        (["um", "er", "hmm"], []),
    ],
)
def test_strip_filler(words, expected):
    assert strip_filler(words) == expected
