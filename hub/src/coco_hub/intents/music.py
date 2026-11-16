"""Music intents: play music (Spotify or Apple Music) and "what's playing".

``music.provider`` (#42) picks who plays. ``spotify`` (default): the Hub searches and drives
the Spotify app on the iPad. ``apple_music``: after the same query-level policy check, the
Hub sends the Display a ``music.play`` command (query + kind) and the Display searches and
plays with MusicKit; the Hub never sees the catalog, so the per-candidate policy check below
only runs for Spotify (docs/apple-music.md).

- ``music.play`` covers "play X" without "on YouTube": "play Raffi", "play some music", "play
  the Frozen soundtrack", "put on my Discover Weekly", "play Taylor Swift on Spotify", "play
  songs by the Wiggles", "play Baby Shark", "shuffle a Christmas playlist" (#95: the
  ``shuffle`` slot turns Spotify's shuffle on). ``parse_music_request`` reads the kind (artist /
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
import string

from coco_hub.intents.engine import Intent, IntentContext, IntentRegistry, LlmSlot, Match, Reply
from coco_hub.media.policy import MediaDecision, MediaRequest, policy_from
from coco_hub.media.spotify.errors import NOT_CONFIGURED_SAY, SpotifyError
from coco_hub.media.spotify.service import MUSIC_KINDS, MusicKind, SpotifyService
from coco_hub.media.youtube import spoken_title
from coco_hub.protocol import MusicPlay, NowPlayingView, UtteranceRequest

NO_RESULTS = "I couldn't find that on Spotify."
NOTHING_PLAYING = "Nothing's playing on Spotify."
PLAYING_MUSIC = "Playing music on Spotify."
SHUFFLING_MUSIC = "Shuffling music on Spotify."
APPLE_PLAYING_MUSIC = "Playing music on Apple Music."
APPLE_NOW_PLAYING = "Here's what's playing."

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
        (r"(?:the |my |our |an? )?(?P<q>.+?) (?:album|record)", "album"),
        (r"(?:the |my |our )?playlist (?P<q>.+)", "playlist"),
        (r"(?:the |my |our |an? )?(?P<q>.+?) playlist", "playlist"),
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


def apple_music(ctx: IntentContext) -> bool:
    """True when ``music.provider = "apple_music"``: the Display plays music, not Spotify."""
    return ctx.settings.music.provider == "apple_music"


def _apple_music_play(query: str, kind: MusicKind) -> Reply:
    """The policy already allowed ``query``; the Display searches and plays it."""
    say = f"Playing {string.capwords(query)} on Apple Music." if query else APPLE_PLAYING_MUSIC
    return Reply(say=say, view=NowPlayingView(), commands=(MusicPlay(query=query, kind=kind),))


def _shuffle(match: Match) -> bool:
    """The rules capture the shuffle wording; Qwen's boolean arrives as "true" / "false"."""
    return match.slots.get("shuffle", "false") not in ("", "false")


async def music_play(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    asked = " ".join(match.slots.get("query", "").split())
    shuffle = _shuffle(match)
    kind, query = parse_music_request(asked)
    if match.slots.get("kind") in MUSIC_KINDS:
        kind = match.slots["kind"]  # type: ignore[assignment]  # from Qwen
    policy = policy_from(ctx.services)
    decision = policy.check(MediaRequest(kind="music", query=query or asked or "music"))
    if not decision.allowed:
        return Reply(say=decision.say)
    if apple_music(ctx):
        return _apple_music_play(query, kind)
    spotify = _spotify(ctx)
    if spotify is None:
        return Reply(say=NOT_CONFIGURED_SAY)
    try:
        spotify.ensure_ready()
        if not query:
            await spotify.play(None, shuffle=shuffle)
            return Reply(say=SHUFFLING_MUSIC if shuffle else PLAYING_MUSIC, view=NowPlayingView())
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
                await spotify.play(item, shuffle=shuffle)
                verb = "Shuffling" if shuffle else "Playing"
                return Reply(
                    say=f"{verb} {spoken_name(item.name)} on Spotify.", view=NowPlayingView()
                )
            first_denial = first_denial or decision
    except SpotifyError as exc:
        return Reply(say=exc.say)
    return Reply(say=first_denial.say if first_denial else NO_RESULTS)


async def music_now_playing(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    if apple_music(ctx):
        # Only the Display knows what MusicKit is playing: show it on screen.
        return Reply(say=APPLE_NOW_PLAYING, view=NowPlayingView())
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
# "shuffle X", "shuffle play X"; or "play X on shuffle" / "play X shuffled".
_SHUFFLE = r"(?P<shuffle>shuffle)(?: play| me)?"
_ON_SHUFFLE = r" (?P<shuffle>on shuffle|shuffled)"
_THIS = r"(?:this|that)"
_SONG = r"(?:song|music|track|tune)"


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "music.play",
            patterns=[
                rf"{_MPLAY} (?:me )?{_NOT_TRANSPORT}{_Q} on spotify",
                rf"{_MPLAY} (?:me )?{_NOT_TRANSPORT}{_NOT_OTHER}{_Q}",
                rf"{_SHUFFLE} {_NOT_TRANSPORT}{_Q} on spotify",
                rf"{_SHUFFLE} {_NOT_TRANSPORT}{_NOT_OTHER}{_Q}",
                rf"{_MPLAY} (?:me )?{_NOT_TRANSPORT}{_Q}{_ON_SHUFFLE}(?: on spotify)?",
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
                        "playlist or song name, or a theme like 'Christmas' for 'shuffle a "
                        "Christmas playlist'); leave it out only for just 'some music'."
                    ),
                ),
                # Same keys and enum as the Qwen spike's schema (docs/spikes/qwen.md).
                "kind": LlmSlot(
                    enum=MUSIC_KINDS,
                    hint="kind is artist, album, playlist, song, or any if unsure.",
                ),
                "shuffle": LlmSlot(
                    type="boolean",
                    hint="shuffle is true when the person said shuffle or shuffled.",
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
