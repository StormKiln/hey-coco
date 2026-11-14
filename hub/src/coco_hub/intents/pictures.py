"""Picture intents: "show me a fire truck" → Wikimedia Commons, "next one" → the next result.

Handlers use the ``PictureService`` in ``ctx.services["pictures"]``, which also remembers the
last result for "next" (one Display at a time).
"""

from coco_hub.intents.engine import (
    Intent,
    IntentContext,
    IntentRegistry,
    LlmSlot,
    Match,
    Reply,
)
from coco_hub.pictures.commons import PictureFetchError, PictureResult, PictureService
from coco_hub.protocol import PictureView, UtteranceRequest

NOT_FOUND = "I couldn't find a picture of that."
UNAVAILABLE = "Sorry, I can't get pictures right now."
NOTHING_SHOWN = "Ask me to show you something first."
ONLY_ONE = "That's the only picture I found."

_ART = r"(?:(?:a|an|the|some|any) )?"
_PIC = r"(?:(?:a|an|some|any|the) )?(?:pictures?|photos?|photographs?|pics?|images?) of "
# What follows "show me"/"can I see" — but not other features' requests ("show me the
# timers", "show me how to …", "show me a video of …", "show me the next one").
_NOT_A_THING = (
    r"(?!(?:the |my |a |an |some )?(?:timers?|weather|forecast|videos?|movies?|cartoons?|"
    r"songs?|music|clips?|episodes?|next|another|more|different|time|date)\b)"
    r"(?!(?:how|what|where|when|why|who|me|us|it|that|this)\b)"
)
_QUERY = rf"{_NOT_A_THING}(?P<query>[a-z0-9][a-z0-9 ]*?)"
_SHOW = r"(?:show|find|get|search for|look up)(?: me| us)?"
_CAN_SEE = r"(?:(?:can|could|may) (?:i|we)|i want to|i would like to|let us|lets) (?:see|look at)"

_SHOW_PATTERNS = [
    rf"{_SHOW} {_PIC}{_ART}{_QUERY}",
    rf"{_SHOW} {_ART}{_QUERY}",
    rf"{_CAN_SEE} {_PIC}{_ART}{_QUERY}",
    rf"{_CAN_SEE} {_ART}{_QUERY}",
    rf"{_PIC}{_ART}{_QUERY}",
    rf"what (?:does|do|is) {_ART}{_QUERY} look like",
    rf"{_SHOW} what {_ART}{_QUERY} (?:looks?|look) like",
]
_NEXT_PATTERNS = [
    r"(?:show me |give me )?(?:the )?next(?: one| picture| photo| image)?",
    r"(?:show me |give me )?another(?: one| picture| photo| image)?",
    r"(?:show me |give me )?(?:a )?different (?:one|1|picture|photo|image)",
    r"(?:show me |give me )?(?:some )?more(?: pictures| photos)?",
]


def _pictures(ctx: IntentContext) -> PictureService:
    return ctx.services["pictures"]


def _is_plural(query: str) -> bool:
    last = query.rsplit(" ", 1)[-1]
    return last.endswith("s") and not last.endswith(("ss", "us", "is"))


def here_is(query: str) -> str:
    """``Here's a fire truck.`` / ``Here's an axolotl.`` / ``Here are some penguins.``"""
    if _is_plural(query):
        return f"Here are some {query}."
    article = "an" if query[:1] in "aeiou" else "a"
    return f"Here's {article} {query}."


def _view(result: PictureResult) -> PictureView:
    return PictureView(query=result.query, images=result.current)


async def picture_show(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    query = match.slots.get("query", "").strip()
    if not query:
        return Reply(say=NOT_FOUND)
    service = _pictures(ctx)
    try:
        images = await service.search(query)
    except PictureFetchError:
        return Reply(say=UNAVAILABLE)
    if not images:
        return Reply(say=NOT_FOUND)
    return Reply(say=here_is(query), view=_view(service.remember(query, images)))


def picture_next(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    service = _pictures(ctx)
    if service.last is None:
        return Reply(say=NOTHING_SHOWN)
    result = service.advance()
    if result is None:
        return Reply(say=ONLY_ONE)
    return Reply(say=None, view=_view(result))


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "picture.show",
            patterns=_SHOW_PATTERNS,
            handler=picture_show,
            description=('show a photo or picture of a thing; also "what does an X look like"'),
            llm_slots={
                "query": LlmSlot(
                    required=True,
                    hint=(
                        'picture query is just the thing to show, without "a" or "the": '
                        '"pull up a photo of the eiffel tower" -> "eiffel tower".'
                    ),
                )
            },
        )
    )
    registry.register(
        Intent(
            "picture.next",
            patterns=_NEXT_PATTERNS,
            handler=picture_next,
            # Beats other "next" intents (e.g. media) while pictures are on screen.
            priority=1,
            applies=lambda c: c.view == "picture",
            # Offered to Qwen; the fallback refuses it unless pictures are on screen.
            description="another or the next picture, only while pictures are on screen",
        )
    )
