"""#132: the read-only Kasa probe (``make kasa-probe HOST=...``), against fake devices."""

import itertools
import re
from pathlib import Path

import pytest
from kasa import DeviceEncryptionType

from coco_hub.devices.backends import kasa_probe
from coco_hub.devices.errors import AuthRequired, Unreachable

from fake_kasa import FakeDiscover, FakeKasaDevice, strip

HOST = "192.0.2.10"  # documentation range (RFC 5737)


def ticking_clock(step_ms: float = 5.0):
    counter = itertools.count()
    return lambda: next(counter) * step_ms / 1000


async def run(device: FakeKasaDevice, **kwargs) -> kasa_probe.ProbeResult:
    kwargs.setdefault("clock", ticking_clock())
    return await kasa_probe.run_probe(HOST, discover=FakeDiscover({HOST: device}), **kwargs)


@pytest.mark.anyio
async def test_legacy_device_needs_no_credentials():
    result = await run(FakeKasaDevice(alias="Desk lamp", on=True))
    info = result.info
    assert info.alias == "Desk lamp"
    assert info.model == "XX100(US)"
    assert info.is_on is True
    assert info.needs_credentials is False
    assert "legacy" in info.protocol.lower()
    assert info.outlets == ()


@pytest.mark.anyio
async def test_klap_device_reports_that_it_needs_the_login():
    device = FakeKasaDevice(encryption=DeviceEncryptionType.Klap, credentials=("u@x.test", "pw"))
    result = await run(device, credentials=("u@x.test", "pw"))
    assert result.info.needs_credentials is True
    assert "klap" in result.info.protocol.lower()
    assert result.used_credentials is True


@pytest.mark.anyio
async def test_klap_without_credentials_is_auth_required():
    device = FakeKasaDevice(encryption=DeviceEncryptionType.Klap, credentials=("u@x.test", "pw"))
    with pytest.raises(AuthRequired):
        await run(device)


@pytest.mark.anyio
async def test_unreachable_host():
    with pytest.raises(Unreachable):
        await run(FakeKasaDevice(unreachable=True))


@pytest.mark.anyio
async def test_multi_outlet_lists_children():
    result = await run(strip(("Fan", True), ("Kettle", False)))
    assert [(o.index, o.alias, o.is_on) for o in result.info.outlets] == [
        (0, "Fan", True),
        (1, "Kettle", False),
    ]


@pytest.mark.anyio
async def test_read_only_by_default_and_times_n_reads():
    device = FakeKasaDevice(on=False)
    result = await run(device, reads=10)
    assert device.commands == []  # never touched the relay
    assert len(result.reads_ms) == 10
    assert result.toggle is None
    assert device.disconnected


@pytest.mark.anyio
async def test_toggle_flips_and_restores_the_chosen_outlet():
    device = strip(("Fan", False), ("Kettle", True))
    result = await run(device, toggle=True, child="Fan")
    t = result.toggle
    assert t is not None and t.target == "Fan"
    assert (t.before, t.after_flip, t.after_restore) == (False, True, False)
    assert t.ok
    assert device.commands == [("Fan", True), ("Fan", False)]
    assert device.children[1].on is True  # the other outlet was left alone


@pytest.mark.anyio
async def test_toggle_child_by_index():
    result = await run(strip(("Fan", False), ("Kettle", True)), toggle=True, child="1")
    assert result.toggle.target == "Kettle"


@pytest.mark.anyio
async def test_toggle_reports_a_device_that_ignores_commands():
    result = await run(FakeKasaDevice(on=False, stuck=True), toggle=True)
    assert result.toggle.ok is False


@pytest.mark.anyio
async def test_unknown_child_is_an_error():
    with pytest.raises(ValueError, match="no outlet"):
        await run(strip(("Fan", False)), toggle=True, child="Toaster")


def test_percentiles():
    assert kasa_probe.percentile([10, 20, 30, 40], 50) == 20
    assert kasa_probe.percentile([float(n) for n in range(1, 21)], 95) == 19


@pytest.mark.anyio
async def test_report_has_the_facts_and_no_secrets():
    device = FakeKasaDevice(
        encryption=DeviceEncryptionType.Klap, credentials=("u@x.test", "s3cr3t")
    )
    result = await run(device, credentials=("u@x.test", "s3cr3t"), reads=4)
    text = kasa_probe.format_report(HOST, result)
    for expected in ("XX100(US)", "Desk lamp", "KLAP", "p50", "p95", "1.0.0"):
        assert expected in text
    assert "s3cr3t" not in text and "u@x.test" not in text


def test_credentials_come_from_backends_kasa_in_the_config(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.local.toml"
    cfg.write_text('[backends.kasa]\nusername = "u@x.test"\npassword = "pw"\n')
    monkeypatch.setenv("COCO_CONFIG", str(cfg))
    assert kasa_probe.configured_credentials() == ("u@x.test", "pw")


def test_no_credentials_configured(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("COCO_CONFIG", str(tmp_path / "absent.toml"))
    assert kasa_probe.configured_credentials() is None


def test_only_the_kasa_backend_imports_python_kasa():
    src = Path(kasa_probe.__file__).resolve().parents[2]  # coco_hub/
    allowed = src / "devices" / "backends" / "kasa.py"
    pattern = re.compile(r"^\s*(?:import kasa\b|from kasa\b)", re.MULTILINE)
    offenders = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if p != allowed and pattern.search(p.read_text())
    ]
    assert offenders == []
