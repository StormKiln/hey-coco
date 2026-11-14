"""Built-in intents: the time and the date, in the Hub's timezone."""

from datetime import datetime

from coco_hub.intents.engine import Intent, IntentContext, IntentRegistry, Match, Reply
from coco_hub.protocol import AnswerView, UtteranceRequest

_NOW = r"(?: now| right now)?"


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


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "time.now",
            patterns=[
                rf"(?:tell me )?what time is it{_NOW}",
                rf"(?:tell me )?what time it is{_NOW}",
                rf"what is the time{_NOW}",
                rf"(?:tell me |what )?(?:the )?time{_NOW}",
            ],
            handler=time_now,
            description="Say the current time",
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
            description="Say today's day and date",
        )
    )
