"""#134: "turn on / off <device>" and "is <device> on", with fictional devices only."""

import json
from pathlib import Path

import pytest

from coco_hub.devices.model import Capability
from coco_hub.devices.service import DeviceService
from coco_hub.intents import build_registry
from coco_hub.intents.engine import IntentContext, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import AnswerView, UtteranceContext, UtteranceRequest
from coco_hub.qwen.catalog import Catalog
from coco_hub.settings import BackendSettings, DeviceConfig

from fake_backend import FakeBackend, FakeDevice

QUIET = UtteranceContext(view="idle", alarm_ringing=False, media_playing=None)
RINGING = UtteranceContext(view="timers", alarm_ringing=True, media_playing=None)
EVAL = Path(__file__).resolve().parents[2] / "eval" / "utterances.jsonl"


def make(devices: dict[str, FakeDevice], *entries: DeviceConfig, timeout_s: float = 2.0):
    backend = FakeBackend(devices)
    svc = DeviceService(
        list(entries), {"fake": backend}, {"fake": BackendSettings(timeout_s=timeout_s)}
    )
    registry = build_registry(device_names=svc.spoken_names)
    return svc, registry, backend


def entry(id_: str, name: str | None = None, **kw) -> DeviceConfig:
    return DeviceConfig(
        name=name, backend="fake", options={"id": id_, **kw.pop("options", {})}, **kw
    )


@pytest.fixture
def home():
    devices = {
        "lamp": FakeDevice("Plug 1", on=False),
        "fan": FakeDevice("Plug 2", on=True),
        "strip": FakeDevice("Strip", outlets=[["Kettle", True], ["Radio", False]]),
    }
    return make(
        devices,
        entry("lamp", "desk lamp", aliases=["lamp"]),
        entry("fan", "fan"),
        entry("strip", "power strip"),
    )


async def say(svc, registry, text: str, context=QUIET):
    engine = RulesEngine(registry)
    ctx = IntentContext(settings=None, services={"devices": svc})  # type: ignore[arg-type]
    action = await engine.handle(UtteranceRequest(text=text, source="voice", context=context), ctx)
    return action


# --- matching ----------------------------------------------------------------------------

MATCHES = [
    ("turn on the desk lamp", "device.power", {"device": "desk lamp", "state": "on"}),
    (
        "Hey Coco, turn the desk lamp off please",
        "device.power",
        {"device": "desk lamp", "state": "off"},
    ),
    ("switch on the fan", "device.power", {"device": "fan", "state": "on"}),
    ("switch the fan off", "device.power", {"device": "fan", "state": "off"}),
    ("fan on", "device.power", {"device": "fan", "state": "on"}),
    ("turn off lamp", "device.power", {"device": "lamp", "state": "off"}),
    ("is the desk lamp on", "device.state", {"device": "desk lamp"}),
    ("is the fan off?", "device.state", {"device": "fan"}),
    ("is the power strip turned on", "device.state", {"device": "power strip"}),
    ("dim the desk lamp", "device.brightness", {"device": "desk lamp"}),
    ("turn on the toaster", "device.unknown", {"device": "toaster", "state": "on"}),
    ("turn the porch light off", "device.unknown", {"device": "porch light", "state": "off"}),
    ("is the heater on", "device.unknown", {"device": "heater"}),
]


@pytest.mark.parametrize(("text", "intent", "slots"), MATCHES)
def test_matches(home, text, intent, slots):
    _, registry, _ = home
    match = registry.match(normalize(text), QUIET)
    assert match is not None and match.intent == intent
    for key, value in slots.items():
        assert match.slots.get(key) == value


def test_patterns_follow_the_configured_names():
    svc, registry, _ = make({"x": FakeDevice("X")}, entry("x", "garden pump"))
    assert registry.match("turn on the garden pump").intent == "device.power"
    assert registry.match("turn on the desk lamp").intent == "device.unknown"


@pytest.mark.anyio
async def test_names_learned_from_devices_after_start():
    svc, registry, _ = make({"x": FakeDevice("Hall Light")}, entry("x"))
    assert registry.match("turn on the hall light").intent == "device.unknown"
    await svc.start()
    assert registry.match("turn on the hall light").intent == "device.power"


@pytest.mark.parametrize(
    "text",
    [
        "turn the music off",
        "turn off the timer",
        "switch off the alarm",
        "turn it off",
        "turn the sound off",
        "turn up the volume",
        "is it on",
        "turn the oven on to 400",
    ],
)
def test_catch_all_leaves_cocos_own_features_alone(home, text):
    _, registry, _ = home
    match = registry.match(normalize(text), QUIET)
    assert match is None or not match.intent.startswith("device.")


def test_device_intents_are_never_offered_to_qwen(home):
    _, registry, _ = home
    catalog = Catalog.from_registry(registry)
    assert not [n for n in catalog.intent_names if n.startswith("device.")]


def test_without_devices_only_the_catch_all_exists():
    registry = build_registry()
    assert registry.match("turn on the fan").intent == "device.unknown"


# --- no regressions: every eval utterance matches as it did before device intents ----------


def test_eval_utterances_do_not_regress(home):
    _, with_devices, _ = home
    without = build_registry(include_devices=False)
    for line in EVAL.read_text().splitlines():
        case = json.loads(line)
        ctx = UtteranceContext(
            view="idle",
            alarm_ringing=bool(case.get("context", {}).get("alarm_ringing")),
            media_playing=case.get("context", {}).get("media_playing"),
        )
        text = normalize(case["text"])
        before = without.match(text, ctx)
        after = with_devices.match(text, ctx)
        if before is not None:
            assert after is not None and after.intent == before.intent, case["text"]
        elif after is not None:
            # Newly caught by the device catch-all: only requests Coco can't do anyway.
            assert after.intent.startswith("device.") and case["intent"] in (
                "none",
                after.intent,
            ), case["text"]


# --- replies -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_turn_on_confirms_after_reading_back(home):
    svc, registry, backend = home
    action = await say(svc, registry, "turn on the desk lamp")
    assert action.say == "Desk lamp is on."
    assert action.view == AnswerView(text="Desk lamp is on.")
    assert action.intent == "device.power" and action.matched_by == "rules"
    assert backend.devices["lamp"].on is True


@pytest.mark.anyio
async def test_already_on(home):
    svc, registry, backend = home
    action = await say(svc, registry, "turn on the fan")
    assert action.say == "Fan was already on."
    assert backend.devices["fan"].commands == []


@pytest.mark.anyio
async def test_alias_replies_with_the_configured_name(home):
    svc, registry, _ = home
    assert (await say(svc, registry, "turn on lamp")).say == "Desk lamp is on."


@pytest.mark.anyio
async def test_device_that_ignores_the_command_is_reported_honestly():
    svc, registry, _ = make({"x": FakeDevice("X", on=False, stuck=True)}, entry("x", "fan"))
    action = await say(svc, registry, "turn on the fan")
    assert action.say == "I asked the fan to turn on, but it still says it's off."


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("is the desk lamp on", "Desk lamp is off."),
        ("is the fan on", "Fan is on."),
        ("is the fan off", "Fan is on."),
        ("is the power strip on", "Power strip is partly on: Kettle is on, Radio is off."),
    ],
)
async def test_state_answers(home, text, expected):
    svc, registry, _ = home
    assert (await say(svc, registry, text)).say == expected


@pytest.mark.anyio
async def test_mixed_outlets_after_a_partial_switch():
    devices = {"s": FakeDevice("S", outlets=[["A", False], ["B", False]], stuck=True)}
    svc, registry, _ = make(devices, entry("s", "power strip"))
    action = await say(svc, registry, "turn on the power strip")
    assert "still says it's off" in action.say


@pytest.mark.anyio
async def test_unreachable(home):
    svc, registry, backend = home
    backend.devices["lamp"].offline = True
    action = await say(svc, registry, "turn on the desk lamp")
    assert action.say == "I couldn't reach the desk lamp. It may be unplugged or off the network."
    assert backend.devices["lamp"].on is False


@pytest.mark.anyio
async def test_slow_device_is_unreachable_not_success():
    svc, registry, _ = make({"x": FakeDevice("X", delay_s=1)}, entry("x", "fan"), timeout_s=0.05)
    assert (await say(svc, registry, "turn on the fan")).say.startswith("I couldn't reach the fan")


@pytest.mark.anyio
async def test_auth_required():
    svc, registry, _ = make({"x": FakeDevice("X", needs_login=True)}, entry("x", "fan"))
    action = await say(svc, registry, "turn off the fan")
    assert (
        action.say == "The fan needs a login before I can control it. See the Hub's device setup."
    )


@pytest.mark.anyio
async def test_unknown_name(home):
    svc, registry, _ = home
    assert (await say(svc, registry, "turn on the toaster")).say == (
        "I don't know a device called toaster."
    )


@pytest.mark.anyio
async def test_unknown_name_with_no_devices_set_up():
    svc, registry, _ = make({})
    assert (await say(svc, registry, "turn on the fan")).say == "No devices are set up yet."


@pytest.mark.anyio
async def test_cannot_dim_a_plug(home):
    svc, registry, _ = home
    assert (await say(svc, registry, "dim the desk lamp")).say == "The desk lamp can't dim."


@pytest.mark.anyio
async def test_cannot_switch_a_device_without_power():
    devices = {"x": FakeDevice("X", capabilities=frozenset({Capability.BRIGHTNESS}))}
    svc, registry, _ = make(devices, entry("x", "sensor"))
    assert (await say(svc, registry, "turn on the sensor")).say == (
        "The sensor can't be switched on or off."
    )


@pytest.mark.parametrize("text", ["turn it off", "turn the alarm off", "switch off the alarm"])
def test_alarm_still_wins_while_ringing(home, text):
    _, registry, _ = home
    assert registry.match(normalize(text), RINGING).intent == "alarm.stop"
