"""Music intents: play on Spotify and "what's playing".

- ``music.play`` covers "play X" without "on YouTube": "play Raffi", "play some music", "play
  the Frozen soundtrack", "put on my Discover Weekly", "play Taylor Swift on Spotify", "play
  songs by the Wiggles", "play Baby Shark". ``parse_music_request`` reads the kind (artist /
  album / playlist / song / any) from the wording; "any" searches every kind and ``rank``
  picks the best. A bare "play some music" resumes whatever Spotify last had on the iPad.
- ``music.now_playing``: "what's playing", "what song is this", "who sings this".

Every request goes through the media policy (``policy_from``, kind ``music``): once with the
query before searching, then once per candidate (``item_id`` = Spotify URI, ``title``,
``channel`` = artist / playlist owner); the first allowed candidate plays. Pause / resume /
next / stop / volume for music live in ``intents/media.py`` (they target
``context.media_playing``).

The play patterns step aside for YouTube, videos, sounds, pictures, games and timers, and for
the transport phrases ("play it again", "play the next one", "play something else").
"""

import re

from coco_hub.intents.engine import Intent, IntentContext, IntentRegistry, LlmSlot, Match, Reply
from coco_hub.media.policy import MediaDecision, MediaRequest, policy_from
from coco_hub.media.spotify.errors import NOT_CONFIGURED_SAY, SpotifyError
from coco_hub.media.spotify.service import MUSIC_KINDS, MusicKind, SpotifyService
from coco_hub.media.youtube import spoken_title
from coco_hub.protocol import NowPlayingView, UtteranceRequest

NO_RESULTS = "I couldn't find that on Spotify."
NOTHING_PLAYING = "Nothing's playing on Spotify."
PLAYING_MUSIC = "Playing music on Spotify."

# Requests with nothing to search for: resume Spotify on the iPad.
_GENERIC = frozenset(
    {
        "music",
        "songs",
        "a song",
        "tunes",
        "something",
        "anything",
        "my music",
        "our music",
        "spotify",
        "my spotify",
        "some",
    }
)
_LEAD = re.compile(r"(?:some|a little|a bit of|any) ")
_RULES: tuple[tuple[re.Pattern[str], MusicKind], ...] = tuple(
    (re.compile(pattern), kind)
    for pattern, kind in (
        (
            r"(?:songs|music|something|anything|stuff|tracks|a song) (?:by|from) (?P<q>.+)",
            "artist",
        ),
        (r"(?:the )?(?:artist|band|singer|group) (?P<q>.+)", "artist"),
        (r"(?:the )?(?:album|record) (?P<q>.+)", "album"),
        (r"(?:the )?(?P<q>.+? soundtrack)", "album"),
        (r"(?:the |my |our )?(?P<q>.+?) (?:album|record)", "album"),
        (r"(?:the |my |our )?playlist (?P<q>.+)", "playlist"),
        (r"(?:the |my |our )?(?P<q>.+?) playlist", "playlist"),
        (r"(?:my|our) (?P<q>.+)", "playlist"),
        (r"(?:the )?(?:song|track|tune) (?P<q>.+)", "song"),
        (r"(?:the )?(?P<q>.+?) (?:song|track|tune)", "song"),
        (r"(?P<q>.+?) (?:songs|music|tunes)", "any"),
    )
)


def parse_music_request(query: str) -> tuple[MusicKind, str]:
    """(kind, search text) for what was asked; search text "" means "just play music"."""
    text = " ".join(query.split())
    text = re.sub(r" on spotify$", "", text)
    if text in _GENERIC:
        return ("any", "")
    text = _LEAD.sub("", text, count=1) if _LEAD.match(text) else text
    if text in _GENERIC:
        return ("any", "")
    for pattern, kind in _RULES:
        m = pattern.fullmatch(text)
        if m:
            return (kind, m.group("q").strip())
    return ("any", text)


# A trailing "(...)" / "[...]", or " - ... Remaster / Version / Edit / Live ...".
_DECORATION = re.compile(
    r"\s+[(\[][^)\]]*[)\]]\s*$"
    r"|\s+-\s+(?:.*\b)?(?:remaster(?:ed)?|version|edit|live|mono|stereo)\b.*$",
    re.IGNORECASE,
)


def spoken_name(name: str) -> str:
    """A Spotify name without "(Original Motion Picture Soundtrack)" / "- 2009 Remaster"
    decorations, short enough to say."""
    trimmed = _DECORATION.sub("", name).strip()
    return spoken_title(trimmed or name)


# --- handlers ----------------------------------------------------------------------------


def _spotify(ctx: IntentContext) -> SpotifyService | None:
    return ctx.services.get("spotify")


async def music_play(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    asked = " ".join(match.slots.get("query", "").split())
    kind, query = parse_music_request(asked)
    if match.slots.get("kind") in MUSIC_KINDS:
        kind = match.slots["kind"]  # type: ignore[assignment]  # from Qwen
    policy = policy_from(ctx.services)
    decision = policy.check(MediaRequest(kind="music", query=query or asked or "music"))
    if not decision.allowed:
        return Reply(say=decision.say)
    spotify = _spotify(ctx)
    if spotify is None:
        return Reply(say=NOT_CONFIGURED_SAY)
    try:
        spotify.ensure_ready()
        if not query:
            await spotify.play(None)
            return Reply(say=PLAYING_MUSIC, view=NowPlayingView())
        items = await spotify.search(query, kind)
        first_denial: MediaDecision | None = None
        for item in items:
            decision = policy.check(
                MediaRequest(
                    kind="music",
                    query=query,
                    item_id=item.uri,
                    title=item.name,
                    channel=item.artist,
                )
            )
            if decision.allowed:
                await spotify.play(item)
                return Reply(
                    say=f"Playing {spoken_name(item.name)} on Spotify.", view=NowPlayingView()
                )
            first_denial = first_denial or decision
    except SpotifyError as exc:
        return Reply(say=exc.say)
    return Reply(say=first_denial.say if first_denial else NO_RESULTS)


async def music_now_playing(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    spotify = _spotify(ctx)
    if spotify is None:
        return Reply(say=NOT_CONFIGURED_SAY)
    try:
        spotify.ensure_ready()
        current = await spotify.currently_playing()
    except SpotifyError as exc:
        return Reply(say=exc.say)
    if current is None:
        return Reply(say=NOTHING_PLAYING)
    by = f" by {current.artist}" if current.artist else ""
    return Reply(say=f"This is {spoken_name(current.title)}{by}.", view=NowPlayingView())


# --- registration ------------------------------------------------------------------------

_MPLAY = (
    r"(?:play|put on|listen to|i want to hear|i want to listen to|we want to hear|"
    r"can we listen to|can we hear|let us listen to|let us hear|start playing|spotify play)"
)
# Transport phrases ("play it again", "play the next one") belong to media.*.
_NOT_TRANSPORT = (
    r"(?!(?:it|that|this|them|again|me|more|next|previous|another|youtube|"
    r"the (?:next|previous|last|same)|a different|something else)\b)"
)
# Videos, sounds, pictures, games and timers are not music.
_NOT_OTHER = (
    r"(?!.*\b(?:youtube|video|videos|clip|clips|cartoon|cartoons|episode|episodes|tv|"
    r"sound|sounds|noise|noises|picture|pictures|photo|photos|game|games|timer|timers|"
    r"alarm|alarms)\b)"
)
_Q = r"(?P<query>[a-z0-9].*?)"
_THIS = r"(?:this|that)"
_SONG = r"(?:song|music|track|tune)"


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "music.play",
            patterns=[
                rf"{_MPLAY} (?:me )?{_NOT_TRANSPORT}{_Q} on spotify",
                rf"{_MPLAY} (?:me )?{_NOT_TRANSPORT}{_NOT_OTHER}{_Q}",
            ],
            handler=music_play,
            description=(
                "Play music on Spotify: an artist, album, playlist or song, e.g. 'play Raffi', "
                "'play the Frozen soundtrack', 'put on my Discover Weekly', or just 'play "
                "some music'. Not YouTube videos and not animal or vehicle sounds"
            ),
            llm_slots={
                "query": LlmSlot(
                    hint=(
                        "for music.play, query is what to play, as said (an artist, album, "
                        "playlist or song name); leave it out for just 'some music'."
                    ),
                ),
                # Same keys and enum as the Qwen spike's schema (docs/spikes/qwen.md).
                "kind": LlmSlot(
                    enum=MUSIC_KINDS,
                    hint="kind is artist, album, playlist, song, or any if unsure.",
                ),
            },
        )
    )
    registry.register(
        Intent(
            "music.now_playing",
            patterns=[
                r"what is (?:playing|on)(?: now| right now)?",
                rf"what {_SONG} is (?:this|that|playing|on)(?: now| right now)?",
                rf"what is {_THIS} {_SONG}(?: called)?",
                rf"what is the name of {_THIS} {_SONG}",
                rf"who (?:sings|is singing|sang|is) {_THIS}(?: {_SONG})?",
                rf"who is {_THIS} by",
                r"what (?:am i|are we) listening to",
            ],
            handler=music_now_playing,
            description="Say which song is playing on Spotify ('what song is this')",
        )
    )
