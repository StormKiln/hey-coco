"""Sound intents: "what does a cow say", "play a fire truck sound", "moo", and "again".

Handlers look sounds up in the ``SoundLibrary`` in ``ctx.services["sounds"]`` (names and
aliases, plural- and article-tolerant). The sound view carries Hub-relative media paths
(``/v1/media/sounds/...``); the Display resolves them against the Hub base URL and sends its
bearer token.

"Again" replays the last sound played. That memory lives in the registry built by
``register`` (one per app, one Display at a time), not in a module global.
"""

import random
import re
from dataclasses import dataclass

from coco_hub.intents.engine import (
    Intent,
    IntentContext,
    IntentRegistry,
    LlmSlot,
    Match,
    Reply,
)
from coco_hub.protocol import SoundView, UtteranceRequest
from coco_hub.sounds import Sound, SoundLibrary

UNKNOWN = "I don't have that sound yet."
NOTHING_PLAYED = "Which sound would you like?"

# What a kid might say on its own to ask for a sound. Each must be a name or alias in the
# library (a test checks), otherwise a bare "moo" would match and then not be found.
SOUND_WORDS = (
    "cock a doodle doo",
    "twit twoo",
    "choo choo",
    "hee haw",
    "nee naw",
    "nee nah",
    "wee woo",
    "beep beep",
    "toot toot",
    "ring ring",
    "ding ding",
    "moo",
    "oink",
    "baa",
    "neigh",
    "cluck",
    "quack",
    "gobble",
    "woof",
    "bark",
    "meow",
    "miaow",
    "wheek",
    "roar",
    "growl",
    "howl",
    "awoo",
    "ribbit",
    "croak",
    "buzz",
    "chirp",
    "hoot",
    "tweet",
    "caw",
    "coo",
    "vroom",
    "honk",
)

# What the sound "says", by sound id. Animals say, vehicles go; anything else is "Here's a …".
SAYS: dict[str, str] = {
    "cow": "moo",
    "pig": "oink",
    "sheep": "baa",
    "goat": "maa",
    "horse": "neigh",
    "donkey": "hee haw",
    "rooster": "cock-a-doodle-doo",
    "chicken": "cluck cluck",
    "duck": "quack",
    "turkey": "gobble gobble",
    "dog": "woof",
    "cat": "meow",
    "lion": "roar",
    "wolf": "awoo",
    "frog": "ribbit",
    "owl": "twit twoo",
    "bird": "tweet tweet",
    "cuckoo": "cuckoo",
    "crow": "caw",
    "pigeon": "coo",
    "bee": "buzz",
    "cricket": "chirp",
    "fire_truck": "nee naw",
    "police_car": "wee woo",
    "steam_train": "choo choo",
    "car": "vroom vroom",
    "car_horn": "beep beep",
    "bicycle": "ring ring",
    "ship": "toot toot",
}

_VEHICLE_WORDS = re.compile(
    r"\b(?:truck|trucks|car|cars|train|trains|engine|plane|planes|jet|boat|boats|ship|bus|"
    r"bike|bikes|motorbike|tractor|digger|rocket|siren|horn|lorry|van|tram|taxi|scooter|"
    r"helicopter|hovercraft|submarine|vehicle|vehicles|craft)\b"
)

_ART = r"(?:(?:a|an|the|some) )?"
# Not a thing that makes a sound: "turn the sound up", "mute the sound", "stop the noise".
_NAME = (
    r"(?!(?:the|that|this|it|some|any|more|less|no|my|your|turn|mute|unmute|stop|pause|"
    r"lower|raise|volume)\b)(?P<name>[a-z][a-z0-9 ]*?)"
)
_NOISE = r"(?:sound|noise|sounds|noises)"
_HEAR = r"(?:let me|let us|can i|can we|could i|could we|i want to) hear"
# "show me a cow sound" / "show me what a lion sounds like" are sounds, not pictures.
_SHOW = r"(?:show|tell)(?: me| us)?"
_WORD = "|".join(re.escape(w) for w in sorted(SOUND_WORDS, key=len, reverse=True))

_PLAY_PATTERNS = [
    rf"what (?:does|do) {_ART}{_NAME} say",
    rf"what {_NOISE} (?:does|do) {_ART}{_NAME} make",
    rf"what (?:does|do) {_ART}{_NAME} sound like",
    rf"how (?:does|do) {_ART}{_NAME} (?:go|sound)",
    rf"what is the {_NOISE} (?:of|from) {_ART}{_NAME}",
    rf"(?:play|make|do)(?: me| us)? {_ART}{_NAME} {_NOISE}",
    rf"(?:play|make|do)(?: me| us)? the {_NOISE} (?:of|from) {_ART}{_NAME}",
    # "hear" needs "a/an" or a trailing "sound/noise", so "I want to hear Let It Go" stays music.
    rf"{_HEAR} (?:a|an) {_NAME}(?: {_NOISE})?",
    rf"{_HEAR} {_ART}{_NAME} {_NOISE}",
    rf"{_ART}{_NAME} {_NOISE}",
    rf"{_SHOW} {_ART}{_NAME} {_NOISE}",
    rf"{_SHOW} what {_ART}{_NAME} (?:says?|sounds? like|goes like)",
    rf"{_SHOW} the {_NOISE} (?:of|from) {_ART}{_NAME}",
    rf"(?P<name>(?:{_WORD})(?: (?:{_WORD}))*)",
]
_AGAIN_PATTERNS = [
    r"(?:play |do |say )?(?:it |that |that sound |the sound )?again",
    r"(?:1|one) more time",
    r"replay(?: it| that)?",
    r"repeat(?: it| that)?",
]


def article(name: str) -> str:
    return f"{'an' if name[:1] in 'aeiou' else 'a'} {name}"


def spoken(sound: Sound) -> str:
    """``A cow says moo!`` / ``A fire truck goes nee naw!`` / ``Here's an elephant.``"""
    word = SAYS.get(sound.id)
    if word is None:
        return f"Here's {article(sound.name)}."
    verb = "says" if sound.category == "animal" else "goes"
    return f"{article(sound.name).capitalize()} {verb} {word}!"


def lookup(library: SoundLibrary, name: str) -> Sound | None:
    """Find by name/alias; a repeated sound word ("moo moo", "nee naw nee naw") by one copy."""
    if sound := library.find(name):
        return sound
    words = name.split()
    for size in range(1, len(words) // 2 + 1):
        unit = words[:size]
        if len(words) % size == 0 and unit * (len(words) // size) == words:
            if sound := library.find(" ".join(unit)):
                return sound
    return None


def view(sound: Sound) -> SoundView:
    return SoundView(
        name=sound.name,
        clip_url=sound.clip_url_path(),
        image_url=sound.image_url_path(),
        attribution=sound.attribution_text(),
    )


@dataclass
class _LastSound:
    sound: Sound | None = None


def register(registry: IntentRegistry, *, rng: random.Random | None = None) -> None:
    last = _LastSound()
    pick = rng or random.Random()

    def suggestion(library: SoundLibrary, name: str) -> str:
        category = "vehicle" if _VEHICLE_WORDS.search(name) else "animal"
        pool = sorted((s for s in library.all() if s.category == category), key=lambda s: s.id)
        if len(pool) < 2:
            return UNKNOWN
        a, b = pick.sample(pool, 2)
        return f"{UNKNOWN} How about {article(a.name)} or {article(b.name)}?"

    def sound_play(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
        library: SoundLibrary = ctx.services["sounds"]
        name = match.slots.get("name", "").strip()
        sound = lookup(library, name) if name else None
        if sound is None:
            return Reply(say=suggestion(library, name))
        last.sound = sound
        return Reply(say=spoken(sound), view=view(sound))

    def sound_again(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
        if last.sound is None:
            return Reply(say=NOTHING_PLAYED)
        # Re-sending the sound view makes the Display play the clip again; nothing to say.
        return Reply(say=None, view=view(last.sound))

    registry.register(
        Intent(
            "sound.play",
            patterns=_PLAY_PATTERNS,
            handler=sound_play,
            description=(
                "play the noise an animal or vehicle makes; also "
                '"what does an X say / sound like / what noise does an X make"'
            ),
            llm_slots={
                "name": LlmSlot(
                    required=True,
                    hint=(
                        "sound name is the plain animal or vehicle, singular: "
                        '"doggo noise" -> "dog", "the noise a police car makes" -> "police car".'
                    ),
                )
            },
        )
    )
    registry.register(
        Intent(
            "sound.again",
            patterns=_AGAIN_PATTERNS,
            handler=sound_again,
            priority=1,
            applies=lambda c: c.view == "sound",
            # Offered to Qwen; the fallback refuses it unless a sound is on screen.
            description="play the sound again, only while a sound is on screen",
        )
    )
