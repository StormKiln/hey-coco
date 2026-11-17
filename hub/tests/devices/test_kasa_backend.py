"""Kasa-only behaviour beyond the shared contract: credentials and the MAC safety check."""

import pytest
from kasa import DeviceEncryptionType

from coco_hub.devices.backends.kasa import KasaBackend
from coco_hub.devices.errors import AuthRequired, Unreachable
from coco_hub.settings import BackendSettings

from fake_kasa import FakeDiscover, FakeKasaDevice

HOST = "192.0.2.20"


def backend(device: FakeKasaDevice, **settings) -> tuple[KasaBackend, FakeDiscover]:
    discover = FakeDiscover({HOST: device})
    return KasaBackend(BackendSettings(**settings), discover=discover), discover


@pytest.mark.anyio
async def test_legacy_device_gets_no_credentials_even_if_configured():
    b, discover = backend(FakeKasaDevice(), username="u@x.test", password="pw")
    await b.connect(b.parse_options({"host": HOST}))
    assert discover.calls[0].get("credentials") is None


@pytest.mark.anyio
async def test_klap_device_uses_configured_login():
    device = FakeKasaDevice(encryption=DeviceEncryptionType.Klap, credentials=("u@x.test", "pw"))
    b, discover = backend(device, username="u@x.test", password="pw")
    handle = await b.connect(b.parse_options({"host": HOST}))
    assert handle.alias == "Desk lamp"
    assert discover.calls[-1]["credentials"].username == "u@x.test"


@pytest.mark.anyio
async def test_klap_device_with_wrong_login():
    device = FakeKasaDevice(encryption=DeviceEncryptionType.Klap, credentials=("u@x.test", "pw"))
    b, _ = backend(device, username="u@x.test", password="wrong")
    with pytest.raises(AuthRequired):
        await b.connect(b.parse_options({"host": HOST}))


@pytest.mark.anyio
async def test_mac_mismatch_refuses_a_different_device_at_that_address():
    b, _ = backend(FakeKasaDevice(mac="00:00:5E:00:53:99"))
    options = b.parse_options({"host": HOST, "mac": "00-00-5e-00-53-01"})
    with pytest.raises(Unreachable, match="different device"):
        await b.connect(options)


@pytest.mark.anyio
async def test_mac_match_in_any_format():
    b, _ = backend(FakeKasaDevice(mac="00:00:5E:00:53:01"))
    await b.connect(b.parse_options({"host": HOST, "mac": "00005e005301"}))


def test_host_is_required():
    with pytest.raises(ValueError):
        KasaBackend(BackendSettings()).parse_options({})
