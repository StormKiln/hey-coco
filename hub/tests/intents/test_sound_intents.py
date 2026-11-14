import random

import pytest

from coco_hub.intents import builtin, pictures, sounds, timers
from coco_hub.intents.engine import IntentContext, IntentRegistry, Match, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import Action, SoundView, UtteranceContext, UtteranceRequest
from coco_hub.settings import DEFAULT_SOUNDS_DIR
from coco_hub.sounds import SoundLibrary

IDLE = UtteranceContext(view="idle", alarm_ringing=False, media_playing=None)
SOUND = UtteranceContext(view="sound", alarm_ringing=False, media_playing=None)


@pytest.fixture(scope="module")
def library() -> SoundLibrary:
    return SoundLibrary.load(DEFAULT_SOUNDS_DIR)


@pytest.fixture
def registry() -> IntentRegistry:
    r = IntentRegistry()
    builtin.register(r)
    timers.register(r)
    pictures.register(r)
    sounds.register(r, rng=random.Random(7))
    return r


PHRASES: list[tuple[str, UtteranceContext, str | None, str | None]] = [
    # sound.play
    ("what does a cow say", IDLE, "sound.play", "cow"),
    ("What does the cow say?", IDLE, "sound.play", "cow"),
    ("what do cows say", IDLE, "sound.play", "cows"),
    ("what sound does a duck make", IDLE, "sound.play", "duck"),
    ("what noise does a pig make", IDLE, "sound.play", "pig"),
    ("what sounds do elephants make", IDLE, "sound.play", "elephants"),
    ("what does a lion sound like", IDLE, "sound.play", "lion"),
    ("how does a sheep go", IDLE, "sound.play", "sheep"),
    ("play a fire truck sound", IDLE, "sound.play", "fire truck"),
    ("make a train noise", IDLE, "sound.play", "train"),
    ("play the sound of a helicopter", IDLE, "sound.play", "helicopter"),
    ("make the noise of a police car", IDLE, "sound.play", "police car"),
    ("do a dog noise", IDLE, "sound.play", "dog"),
    ("fire truck sound", IDLE, "sound.play", "fire truck"),
    ("a horse noise please", IDLE, "sound.play", "horse"),
    ("let me hear a rooster", IDLE, "sound.play", "rooster"),
    ("can I hear an owl", IDLE, "sound.play", "owl"),
    ("I want to hear the tractor noise", IDLE, "sound.play", "tractor"),
    ("what is the sound of a steam train", IDLE, "sound.play", "steam train"),
    ("what does the fox say", IDLE, "sound.play", "fox"),
    ("Hey Coco, what does a cat say", IDLE, "sound.play", "cat"),
    ("moo", IDLE, "sound.play", "moo"),
    ("Moo!", IDLE, "sound.play", "moo"),
    ("choo choo", IDLE, "sound.play", "choo choo"),
    ("quack quack", IDLE, "sound.play", "quack quack"),
    ("woof", IDLE, "sound.play", "woof"),
    ("nee naw nee naw", IDLE, "sound.play", "nee naw nee naw"),
    ("vroom vroom", IDLE, "sound.play", "vroom vroom"),
    ("what does a cow say", SOUND, "sound.play", "cow"),
    # sound.again — only while a sound is on screen
    ("again", SOUND, "sound.again", None),
    ("play it again", SOUND, "sound.again", None),
    ("do it again", SOUND, "sound.again", None),
    ("one more time", SOUND, "sound.again", None),
    ("again please", SOUND, "sound.again", None),
    ("play that again", SOUND, "sound.again", None),
    ("replay", SOUND, "sound.again", None),
    ("again", IDLE, None, None),
    ("play it again", IDLE, None, None),
    # not sounds
    ("what does an axolotl look like", IDLE, "picture.show", None),
    ("show me a cow", IDLE, "picture.show", None),
    ("turn the sound up", IDLE, None, None),
    ("mute the sound", IDLE, None, None),
    ("stop the noise", IDLE, None, None),
    ("what time is it", IDLE, "time.now", None),
    ("play some music", IDLE, None, None),
]


@pytest.mark.parametrize(("phrase", "context", "intent", "name"), PHRASES)
def test_phrase_to_intent(registry, phrase, context, intent, name):
    match = registry.match(normalize(phrase), context)
    if intent is None:
        assert match is None or not match.intent.startswith("sound."), match
        return
    assert match is not None, f"{phrase!r} matched nothing"
    assert match.intent == intent
    if name is not None:
        assert match.slots.get("name") == name


def test_enough_phrases():
    assert sum(1 for p in PHRASES if p[2] == "sound.play") >= 25
    assert len(PHRASES) >= 40


def test_descriptions_and_slots_for_the_fallback(registry):
    play, again = registry.get("sound.play"), registry.get("sound.again")
    assert play is not None and again is not None
    assert play.slot_names == ("name",)
    assert "noise" in play.description.lower()
    assert play.offered_to_llm and play.llm_slots["name"].required
    assert again.description and again.offered_to_llm


def test_every_sound_word_alias_is_a_known_alias(library):
    """The bare-word table must only hold aliases the shipped library knows."""
    for word in sounds.SOUND_WORDS:
        assert library.find(word) is not None, word


def test_says_table_only_names_real_sounds(library):
    for sound_id in sounds.SAYS:
        assert library.get(sound_id) is not None, sound_id


# --- handlers ----------------------------------------------------------------------------


@pytest.fixture
def say(registry, library, settings):
    engine = RulesEngine(registry)
    ctx = IntentContext(settings=settings, services={"sounds": library})

    async def run(text: str, context: UtteranceContext = IDLE) -> Action:
        result = await engine.handle(
            UtteranceRequest(text=text, source="voice", context=context), ctx
        )
        assert isinstance(result, Action), f"{text!r} did not match"
        return result

    return run


@pytest.mark.anyio
async def test_play_cow(say, library):
    action = await say("what does a cow say")
    cow = library.get("cow")
    assert cow is not None
    assert action.say == "A cow says moo!"
    assert action.intent == "sound.play"
    assert action.view == SoundView(
        name="cow",
        clip_url="/v1/media/sounds/clips/cow.m4a",
        image_url="/v1/media/sounds/images/cow.jpg",
        attribution=cow.attribution_text(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("phrase", "sound_id", "spoken"),
    [
        ("moo", "cow", "A cow says moo!"),
        ("what do cows say", "cow", "A cow says moo!"),
        ("choo choo", "steam_train", "A steam train goes choo choo!"),
        ("quack quack", "duck", "A duck says quack!"),
        ("nee naw nee naw", "fire_truck", "A fire truck goes nee naw!"),
        ("play a fire truck sound", "fire_truck", "A fire truck goes nee naw!"),
        ("what sounds do elephants make", "elephant", "Here's an elephant."),
        ("can I hear an owl", "owl", "An owl says twit twoo!"),
        ("make a train noise", "train", "Here's a train."),
        ("vroom vroom", "car", "A car goes vroom vroom!"),
        ("play the sound of an airplane", "airplane", "Here's an airplane."),
    ],
)
async def test_play_table(say, library, phrase, sound_id, spoken):
    action = await say(phrase)
    assert isinstance(action.view, SoundView), phrase
    sound = library.get(sound_id)
    assert sound is not None
    assert action.view.clip_url == sound.clip_url_path()
    assert action.view.image_url == sound.image_url_path()
    assert action.say == spoken


@pytest.mark.anyio
async def test_unknown_sound_apologises_and_suggests_two_of_the_same_category(say, library):
    action = await say("what does the fox say")
    assert action.intent == "sound.play"
    assert action.view is None
    assert action.say is not None
    assert action.say.startswith("I don't have that sound yet. How about ")
    animals = {s.name for s in library.all() if s.category == "animal"}
    suggested = [n for n in animals if f" {n} " in action.say or f" {n}?" in action.say]
    assert len(suggested) == 2, action.say


@pytest.mark.anyio
async def test_unknown_vehicle_suggests_vehicles(say, library):
    action = await say("play a hovercraft sound")
    assert action.view is None and action.say is not None
    vehicles = {s.name for s in library.all() if s.category == "vehicle"}
    assert sum(1 for n in vehicles if f" {n} " in action.say or f" {n}?" in action.say) >= 2


@pytest.mark.anyio
async def test_again_replays_the_last_sound(say):
    first = await say("what does a duck say")
    again = await say("play it again", SOUND)
    assert again.intent == "sound.again"
    assert again.view == first.view
    assert again.say is None


@pytest.mark.anyio
async def test_again_follows_the_latest_sound(say):
    await say("what does a duck say")
    pig = await say("what does a pig say", SOUND)
    again = await say("again", SOUND)
    assert again.view == pig.view


@pytest.mark.anyio
async def test_again_with_nothing_played(say):
    action = await say("again", SOUND)
    assert action.view is None
    assert action.say == "Which sound would you like?"


@pytest.mark.anyio
async def test_unknown_does_not_replace_the_last_sound(say):
    duck = await say("what does a duck say")
    await say("what does the fox say", SOUND)
    again = await say("again", SOUND)
    assert again.view == duck.view


@pytest.mark.anyio
async def test_state_is_per_registry(library, settings):
    """Each app builds its own registry, so "again" never leaks between apps."""
    ctx = IntentContext(settings=settings, services={"sounds": library})
    a, b = IntentRegistry(), IntentRegistry()
    sounds.register(a)
    sounds.register(b)
    req = UtteranceRequest(text="again", source="voice", context=SOUND)
    await RulesEngine(a).dispatch(Match("sound.play", {"name": "cow"}, "cow"), req, ctx)
    action = await RulesEngine(b).dispatch(Match("sound.again", {}, "again"), req, ctx)
    assert action.view is None


@pytest.mark.anyio
async def test_qwen_style_dispatch(registry, library, settings):
    ctx = IntentContext(settings=settings, services={"sounds": library})
    req = UtteranceRequest(text="the big red truck with the siren", source="voice", context=IDLE)
    action = await RulesEngine(registry).dispatch(
        Match("sound.play", {"name": "fire truck"}, req.text), req, ctx, "qwen"
    )
    assert isinstance(action.view, SoundView) and action.view.name == "fire truck"
    assert action.matched_by == "qwen"
