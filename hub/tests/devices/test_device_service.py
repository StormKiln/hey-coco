"""#133: the backend-agnostic DeviceService, against the in-memory fake backend."""

import asyncio
import logging
import time

import pytest

from coco_hub.devices.errors import AuthRequired, UnknownDevice, Unreachable, Unsupported
from coco_hub.devices.model import Capability, PowerState
from coco_hub.devices.service import DeviceService, spoken_key
from coco_hub.settings import BackendSettings, DeviceConfig, Settings

from conftest import TEST_TOKEN
from fake_backend import FakeBackend, FakeDevice


def entry(id_: str, name: str | None = None, **kw) -> DeviceConfig:
    return DeviceConfig(
        name=name, backend="fake", options={"id": id_, **kw.pop("options", {})}, **kw
    )


def service(devices: dict[str, FakeDevice], *entries: DeviceConfig, timeout_s=2.0, retry_s=60.0):
    backend = FakeBackend(devices)
    svc = DeviceService(
        list(entries),
        {"fake": backend},
        {"fake": BackendSettings(timeout_s=timeout_s)},
        retry_s=retry_s,
    )
    return svc, backend


def test_spoken_key_normalises_like_utterances():
    assert spoken_key("The Desk Lamp") == "desk lamp"
    assert spoken_key("Fan Two") == "fan 2"
    assert spoken_key("Sam’s lamp") == "sams lamp"


@pytest.mark.anyio
async def test_configured_name_works_before_connecting():
    svc, backend = service({"a": FakeDevice("Plug A", on=True)}, entry("a", "desk lamp"))
    assert "desk lamp" in svc.spoken_names()
    state = await svc.state("desk lamp")  # connects lazily
    assert state.power == PowerState.ON
    assert backend.devices["a"].connects == 1


@pytest.mark.anyio
async def test_unnamed_device_takes_its_own_alias_after_start():
    svc, _ = service({"a": FakeDevice("Hall Light")}, entry("a"))
    assert svc.spoken_names() == {}
    await svc.start()
    assert svc.spoken_names() == {"hall light": "Hall Light"}


@pytest.mark.anyio
async def test_aliases_resolve_to_the_device():
    svc, _ = service(
        {"a": FakeDevice("Plug A")}, entry("a", "desk lamp", aliases=["lamp", "Reading Light"])
    )
    names = svc.spoken_names()
    assert names == {"desk lamp": "desk lamp", "lamp": "desk lamp", "reading light": "desk lamp"}
    assert svc.display_name("the reading light") == "desk lamp"


@pytest.mark.anyio
async def test_unknown_name():
    svc, _ = service({}, entry("a", "fan"))
    with pytest.raises(UnknownDevice):
        await svc.state("toaster")


@pytest.mark.anyio
async def test_turn_on_reads_back_the_new_state():
    svc, backend = service({"a": FakeDevice("Plug", on=False)}, entry("a", "fan"))
    change = await svc.set_power("fan", True)
    assert change.name == "fan"
    assert change.before.power == PowerState.OFF
    assert change.after.power == PowerState.ON
    assert change.changed and change.confirmed
    assert backend.devices["a"].commands == [True]


@pytest.mark.anyio
async def test_already_on_sends_no_command():
    svc, backend = service({"a": FakeDevice("Plug", on=True)}, entry("a", "fan"))
    change = await svc.set_power("fan", True)
    assert not change.changed and change.confirmed
    assert backend.devices["a"].commands == []


@pytest.mark.anyio
async def test_device_that_ignores_the_command_is_not_confirmed():
    svc, _ = service({"a": FakeDevice("Plug", on=False, stuck=True)}, entry("a", "fan"))
    change = await svc.set_power("fan", True)
    assert not change.confirmed
    assert change.after.power == PowerState.OFF


@pytest.mark.anyio
async def test_mixed_outlets_turn_all_on():
    devices = {"s": FakeDevice("Strip", outlets=[["A", True], ["B", False]])}
    svc, _ = service(devices, entry("s", "strip"))
    assert (await svc.state("strip")).power == PowerState.MIXED
    change = await svc.set_power("strip", True)
    assert change.changed and change.after.power == PowerState.ON


@pytest.mark.anyio
async def test_slow_device_times_out_as_unreachable():
    svc, _ = service({"a": FakeDevice("Plug", delay_s=5)}, entry("a", "fan"), timeout_s=0.05)
    start = time.monotonic()
    with pytest.raises(Unreachable):
        await svc.state("fan")
    assert time.monotonic() - start < 1


@pytest.mark.anyio
async def test_timeout_is_per_call_not_per_request():
    # connect + read + switch + read, each 0.15 s, under a 0.3 s per-call limit
    svc, _ = service({"a": FakeDevice("Plug", delay_s=0.15)}, entry("a", "fan"), timeout_s=0.3)
    change = await svc.set_power("fan", True)
    assert change.confirmed


@pytest.mark.anyio
async def test_a_slow_device_does_not_block_another():
    devices = {"slow": FakeDevice("Slow", delay_s=0.5), "fast": FakeDevice("Fast", on=True)}
    svc, _ = service(devices, entry("slow", "slow"), entry("fast", "fast"))
    slow = asyncio.create_task(svc.state("slow"))
    await asyncio.sleep(0)
    start = time.monotonic()
    assert (await svc.state("fast")).power == PowerState.ON
    assert time.monotonic() - start < 0.25
    await slow


@pytest.mark.anyio
async def test_reconnects_after_the_device_comes_back():
    devices = {"a": FakeDevice("Plug", on=True)}
    svc, _ = service(devices, entry("a", "fan"))
    await svc.state("fan")
    devices["a"].offline = True
    with pytest.raises(Unreachable):
        await svc.state("fan")
    devices["a"].offline = False
    assert (await svc.state("fan")).power == PowerState.ON
    assert devices["a"].connects == 2  # the broken handle was dropped


@pytest.mark.anyio
async def test_auth_required_is_reported():
    svc, _ = service({"a": FakeDevice("Plug", needs_login=True)}, entry("a", "fan"))
    with pytest.raises(AuthRequired):
        await svc.set_power("fan", True)


@pytest.mark.anyio
async def test_device_without_power_capability_is_unsupported():
    devices = {"a": FakeDevice("Sensor", capabilities=frozenset())}
    svc, _ = service(devices, entry("a", "sensor"))
    with pytest.raises(Unsupported):
        await svc.set_power("sensor", True)


@pytest.mark.anyio
async def test_capabilities_of_a_device():
    svc, _ = service({"a": FakeDevice("Plug")}, entry("a", "fan"))
    assert await svc.capabilities("fan") == frozenset({Capability.POWER})


def test_duplicate_configured_names_fail_at_startup():
    with pytest.raises(ValueError, match="lamp"):
        service({}, entry("a", "lamp"), entry("b", "desk", aliases=["The Lamp"]))


@pytest.mark.anyio
async def test_duplicate_device_alias_keeps_the_first_and_warns(caplog):
    devices = {"a": FakeDevice("Lamp"), "b": FakeDevice("Lamp")}
    svc, _ = service(devices, entry("a"), entry("b"))
    with caplog.at_level(logging.WARNING):
        await svc.start()
    assert svc.spoken_names() == {"lamp": "Lamp"}
    assert "lamp" in caplog.text.lower()


@pytest.mark.anyio
async def test_unnamed_device_offline_at_start_is_retried_by_run():
    devices = {"a": FakeDevice("Porch Light", offline=True)}
    svc, _ = service(devices, entry("a"), retry_s=0.01)
    await svc.start()
    assert svc.spoken_names() == {}
    task = asyncio.create_task(svc.run())
    devices["a"].offline = False
    for _ in range(100):
        if svc.spoken_names():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert svc.spoken_names() == {"porch light": "Porch Light"}


@pytest.mark.anyio
async def test_aclose_closes_handles_and_backends():
    svc, backend = service({"a": FakeDevice("Plug")}, entry("a", "fan"))
    await svc.state("fan")
    handle = svc._devices[0].handle
    await svc.aclose()
    assert handle.closed == 1 and backend.closed


def test_from_settings_rejects_an_unknown_backend():
    settings = Settings(token=TEST_TOKEN, devices=[{"name": "fan", "backend": "nope"}])
    with pytest.raises(ValueError, match="nope"):
        DeviceService.from_settings(settings)


def test_from_settings_rejects_bad_backend_options():
    settings = Settings(token=TEST_TOKEN, devices=[{"name": "fan", "backend": "kasa"}])
    with pytest.raises(ValueError, match="fan"):
        DeviceService.from_settings(settings)  # kasa needs a host


def test_from_settings_with_no_devices_builds_no_backends():
    svc = DeviceService.from_settings(Settings(token=TEST_TOKEN))
    assert svc.spoken_names() == {} and svc._backends == {}


def test_from_settings_builds_kasa():
    settings = Settings(
        token=TEST_TOKEN,
        devices=[{"name": "fan", "backend": "kasa", "options": {"host": "192.0.2.30"}}],
    )
    assert DeviceService.from_settings(settings).spoken_names() == {"fan": "fan"}


@pytest.mark.anyio
async def test_secrets_never_reach_the_log(caplog):
    settings = Settings(
        token=TEST_TOKEN,
        backends={"fake": {"password": "hunter2-not-real"}},
        devices=[{"name": "fan", "backend": "fake", "options": {"id": "a"}}],
    )
    svc = DeviceService.from_settings(
        settings, backends={"fake": FakeBackend({"a": FakeDevice("P", needs_login=True)})}
    )
    with caplog.at_level(logging.DEBUG):
        await svc.start()
        with pytest.raises(AuthRequired):
            await svc.state("fan")
    assert "hunter2" not in caplog.text
