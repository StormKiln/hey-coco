"""Media intents: YouTube search, transport controls and volume.

- ``youtube.play`` searches YouTube (``ctx.services["youtube"]``) and shows the top result.
  Every video goes through the media policy (``ctx.services["media_policy"]``, see
  ``coco_hub.media.policy``) via ``_play_first_allowed``: there is no other path to a
  ``YoutubeView``.
- ``media.pause`` / ``media.resume`` / ``media.next`` / ``media.stop`` target whatever the
  Display says is playing (``request.context.media_playing``), so they serve YouTube now and
  Spotify / sounds later. ``media.next`` on YouTube plays the next search result.
- ``volume.set`` / ``volume.change`` are Display commands.
- When music is playing (``media_playing == "music"``) and Spotify is ready, the transport and
  volume intents also drive Spotify (``ctx.services["spotify"]``) on the Hub, and still send
  the Display command so its media state stays in sync. Spotify failures are logged, not
  spoken, except where the household can act on them (resume with the iPad missing, Premium
  required, a device that refuses remote volume). Music play / "what's playing" are in
  ``intents/music.py``.

The transport intents step aside while an alarm rings (``alarm.stop`` owns "stop" then).
Patterns match normalised text (``normalize.py``): lowercase, digits, ``%`` → ``percent``,
``can't`` → ``cannot``.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from coco_hub.intents.engine import Intent, IntentContext, IntentRegistry, Match, Reply
from coco_hub.media.policy import MediaDecision, MediaRequest, policy_from
from coco_hub.media.spotify.errors import (
    SpotifyError,
    SpotifyPremiumRequired,
    SpotifyVolumeUnsupported,
)
from coco_hub.media.youtube import (
    LastResults,
    Video,
    YoutubeService,
    YoutubeUnavailable,
    spoken_title,
)
from coco_hub.protocol import (
    MediaCommand,
    UtteranceContext,
    UtteranceRequest,
    VolumeChange,
    VolumeSet,
    YoutubeView,
)

if TYPE_CHECKING:
    from coco_hub.media.spotify.service import SpotifyService

log = logging.getLogger(__name__)

NOTHING_PLAYING = "Nothing's playing."
NO_RESULTS = "I couldn't find that on YouTube."
NO_MORE = "That's all the videos I found."
VOLUME_RANGE = "Volume goes from 0 to 10."
VOLUME_STEP = 15


def _youtube(ctx: IntentContext) -> YoutubeService:
    return ctx.services["youtube"]


def _play_first_allowed(
    videos: list[Video], start: int, query: str, ctx: IntentContext
) -> Reply | MediaDecision | None:
    """Show the first video at or after ``start`` that the media policy allows.

    Returns the ``Reply`` showing it, else the first denial, else None when there are no
    videos from ``start`` on. This is the only place a ``YoutubeView`` is made.
    """
    policy = policy_from(ctx.services)
    first_denial: MediaDecision | None = None
    for index in range(start, len(videos)):
        video = videos[index]
        decision = policy.check(
            MediaRequest(
                kind="youtube",
                query=query,
                item_id=video.video_id,
                title=video.title,
                channel=video.channel,
            )
        )
        if decision.allowed:
            _youtube(ctx).last = LastResults(query=query, videos=videos, index=index)
            return Reply(
                say=f"Playing {spoken_title(video.title)}.",
                view=YoutubeView(video_id=video.video_id, title=video.title),
            )
        first_denial = first_denial or decision
    return first_denial


# --- handlers ----------------------------------------------------------------------------


async def youtube_play(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    query = " ".join(match.slots["query"].split())
    decision = policy_from(ctx.services).check(MediaRequest(kind="youtube", query=query))
    if not decision.allowed:
        return Reply(say=decision.say)
    try:
        videos = await _youtube(ctx).search(query)
    except YoutubeUnavailable as exc:
        return Reply(say=exc.say)
    found = _play_first_allowed(videos, 0, query, ctx)
    if found is None:
        return Reply(say=NO_RESULTS)
    return found if isinstance(found, Reply) else Reply(say=found.say)


SpotifyCall = Callable[["SpotifyService"], Awaitable[None]]
_ALWAYS_SPOKEN = (SpotifyPremiumRequired, SpotifyVolumeUnsupported)


async def _drive_spotify(
    request: UtteranceRequest, ctx: IntentContext, call: SpotifyCall, *, speak: bool = False
) -> str | None:
    """Run ``call`` against Spotify when music is playing and Spotify is ready.

    Returns a sentence to say on failure (when ``speak`` or the household can fix it), else
    None. Without Spotify (unconfigured / not connected) it does nothing.
    """
    if request.context.media_playing != "music":
        return None
    spotify = ctx.services.get("spotify")
    if spotify is None or not spotify.ready:
        return None
    try:
        await call(spotify)
    except SpotifyError as exc:
        if speak or isinstance(exc, _ALWAYS_SPOKEN):
            return exc.say
        log.warning("Spotify control failed: %s", exc)
    return None


_SPOTIFY_CONTROLS: dict[str, Callable[["SpotifyService"], SpotifyCall]] = {
    "media.pause": lambda s: s.pause,
    "media.resume": lambda s: s.resume,
    "media.next": lambda s: s.skip,
    "media.stop": lambda s: s.stop,
}


def _control(command: str):
    async def handler(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
        target = request.context.media_playing
        if target is None:
            return Reply(say=NOTHING_PLAYING)
        say = await _drive_spotify(
            request,
            ctx,
            lambda s: _SPOTIFY_CONTROLS[command](s)(),
            speak=command == "media.resume",
        )
        return Reply(say=say, commands=(MediaCommand(type=command, target=target),))

    return handler


media_pause = _control("media.pause")
media_resume = _control("media.resume")
media_stop = _control("media.stop")
_media_next_command = _control("media.next")


async def media_next(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    if request.context.media_playing != "youtube":
        return await _media_next_command(match, request, ctx)
    last = _youtube(ctx).last
    if last is None:
        return Reply(say=NO_MORE)
    found = _play_first_allowed(last.videos, last.index + 1, last.query, ctx)
    return found if isinstance(found, Reply) else Reply(say=NO_MORE)


async def volume_set(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    slots = match.slots
    if "max" in slots:
        level = 100.0
    elif "mute" in slots:
        level = 0.0
    else:
        value = float(slots["level"])
        # "volume 5" is the 0-10 scale; "volume 70" / "70 percent" is already a percentage.
        level = value if "percent" in slots or value > 10 else value * 10
    if not 0 <= level <= 100:
        return Reply(say=VOLUME_RANGE)
    percent = round(level)
    say = await _drive_spotify(request, ctx, lambda s: s.set_volume(percent))
    return Reply(say=say, commands=(VolumeSet(level=percent),))


async def volume_change(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    delta = VOLUME_STEP if "up" in match.slots else -VOLUME_STEP
    say = await _drive_spotify(request, ctx, lambda s: s.change_volume(delta))
    return Reply(say=say, commands=(VolumeChange(delta=delta),))


# --- registration ------------------------------------------------------------------------

_PLAY = (
    r"(?:play|put on|put|show me|show|find me|find|watch|search for|search|look up|open|"
    r"start|i want to watch|can i watch|let us watch|i want)"
)
# "play the next video" / "play another one" belong to media.next.
_NOT_NEXT = r"(?!(?:the |a )?(?:next|another|previous|last|different|same)\b)"
_Q = r"(?P<query>[a-z0-9].*?)"
_VIDEO = r"(?:youtube )?(?:video|videos|clip|clips|cartoon|cartoons)"
_ITEM = r"(?:video|music|song|show|movie|sound|cartoon|youtube|youtube video|track)"
_THING = rf"(?: (?:it|this|that|the {_ITEM}|this {_ITEM}|youtube))?"
_NUM = r"\d+(?:\.\d+)?"
_PCT = r"(?: (?P<percent>percent))?"
_IT = r"(?:it|that|this|the volume|volume|the sound|the music|the video|the tv)"
_BIT = r"(?:(?:a )?(?:bit|little|little bit|tiny bit|lot) )?"
_MORE = r"(?: a (?:bit|little|little bit|lot)| more)?"


def _not_ringing(context: UtteranceContext) -> bool:
    return not context.alarm_ringing


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "youtube.play",
            patterns=[
                rf"(?:{_PLAY} )?(?:me )?{_NOT_NEXT}{_Q} (?:on|from|in|off) youtube",
                rf"(?:{_PLAY} )?(?:on )?youtube {_Q}",
                rf"(?:search|look up|look|check) (?:on )?youtube for {_Q}",
                rf"(?:{_PLAY} )?(?:me )?(?:a |an |some |the )?{_VIDEO} "
                rf"(?:of|about|on|with|for) {_Q}",
                rf"(?:play|put on|show me|find me|find|watch) (?:a |an |some )?{_NOT_NEXT}{_Q} "
                rf"{_VIDEO}",
            ],
            handler=youtube_play,
            description=(
                "Search YouTube and play the top video full screen, e.g. 'play Bluey on "
                "YouTube' or 'show me a video of monster trucks' (slot: query)"
            ),
        )
    )
    registry.register(
        Intent(
            "media.pause",
            patterns=[rf"(?:pause|hold on|hold it|wait){_THING}(?: 1 (?:sec|second|minute))?"],
            handler=media_pause,
            applies=_not_ringing,
            description="Pause whatever is playing (YouTube video, music or sound)",
        )
    )
    registry.register(
        Intent(
            "media.resume",
            patterns=[
                rf"(?:resume|unpause|un pause|continue|carry on|keep going|keep playing|play|"
                rf"start (?:it )?again|go on|play it again){_THING}",
            ],
            handler=media_resume,
            applies=_not_ringing,
            description="Resume paused playback (YouTube video, music or sound)",
        )
    )
    registry.register(
        Intent(
            "media.next",
            patterns=[
                rf"(?:skip|next)(?: it| this| that| this one| that one| one| 1| {_ITEM}|"
                rf" the {_ITEM}| this {_ITEM})?",
                rf"(?:play |go to |put on |show me )?(?:the )?next(?: one| {_ITEM}| episode)",
                rf"(?:play |put on |show me )?(?:another|a different) (?:one|{_ITEM})",
                r"(?:play |put on )?something else",
            ],
            handler=media_next,
            applies=_not_ringing,
            description=("Skip to the next item: the next YouTube search result, or the next song"),
        )
    )
    registry.register(
        Intent(
            "media.stop",
            patterns=[
                rf"(?:stop|end|close|exit|quit|cancel)(?: playing|{_THING})(?: now)?",
                r"(?:stop|end|close|exit|quit)",
                rf"(?:turn|switch|shut) (?:it|that|this|the {_ITEM}|the tv|youtube) off",
                rf"(?:turn|switch|shut) off (?:the )?(?:{_ITEM}|tv)",
            ],
            handler=media_stop,
            applies=_not_ringing,
            description="Stop whatever is playing and close the player",
        )
    )
    registry.register(
        Intent(
            "volume.set",
            patterns=[
                rf"(?:(?:set|turn|change|put|make) )?(?:the )?volume(?: (?:to|at|on))? "
                rf"(?P<level>{_NUM}){_PCT}",
                rf"(?:set|turn|change|put) {_IT} (?:up |down )?to (?P<level>{_NUM}){_PCT}",
                r"(?:(?:set|turn|put) )?(?:the )?volume (?:all the way up |up )?(?:to |at )?"
                r"(?P<max>max|maximum|full)",
                r"(?P<max>max|maximum|full) volume",
                r"(?:turn|crank) (?:it|the volume) all the way (?P<max>up)",
                r"(?P<mute>mute)(?: it| the (?:video|music|sound|volume|tv))?",
            ],
            handler=volume_set,
            description=(
                "Set the volume to a level, 0 to 10 or a percentage, e.g. 'volume 5' "
                "or 'set the volume to 70 percent'; also 'max volume' and 'mute'"
            ),
        )
    )
    registry.register(
        Intent(
            "volume.change",
            patterns=[
                rf"(?:make it |turn it )?{_BIT}(?P<up>louder)",
                rf"(?:turn|crank|pump) {_IT} (?P<up>up){_MORE}",
                rf"(?:turn|crank) (?P<up>up) {_IT}",
                r"volume (?P<up>up)",
                r"(?P<up>raise|increase) the volume",
                r"i (?P<up>cannot hear|can not hear|can barely hear)"
                r"(?: it| that| this| you| anything)?",
                rf"(?:make it |turn it )?{_BIT}(?P<down>quieter|softer)",
                rf"(?:turn) {_IT} (?P<down>down){_MORE}",
                rf"(?:turn) (?P<down>down) {_IT}",
                r"volume (?P<down>down)",
                r"(?P<down>lower|decrease) the volume",
                r"(?:it is |that is |this is |way )?(?P<down>too loud)",
            ],
            handler=volume_change,
            description=(
                "Turn the volume up or down a step, e.g. 'louder', 'turn it down', 'too loud'"
            ),
        )
    )
