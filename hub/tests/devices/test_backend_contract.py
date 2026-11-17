"""Every backend runs the same contract (backend_contract.py) against its own fake device."""

import itertools

import pytest
from kasa import DeviceEncryptionType

from coco_hub.devices.backends.kasa import KasaBackend
from coco_hub.settings import BackendSettings

from backend_contract import BackendContract
from fake_backend import FakeRig
from fake_kasa import FakeDiscover, FakeKasaDevice, strip


class KasaRig:
    """python-kasa's Device/discover faked; addresses from RFC 5737's documentation range."""

    supports_outlets = True

    def __init__(self) -> None:
        self.discover = FakeDiscover()
        self._hosts = (f"192.0.2.{n}" for n in itertools.count(10))

    def backend(self) -> KasaBackend:
        return KasaBackend(BackendSettings(), discover=self.discover)

    def _add(self, device: FakeKasaDevice, **extra) -> dict:
        host = next(self._hosts)
        self.discover.devices[host] = device
        return {"host": host, **extra}

    def device(self, alias, *, on=False, stuck=False):
        return self._add(FakeKasaDevice(alias=alias, on=on, stuck=stuck))

    def outlets(self, alias, outlets, child=None):
        extra = {} if child is None else {"child": child}
        return self._add(strip(*outlets, alias=alias), **extra)

    def missing(self):
        return {"host": "192.0.2.254"}

    def needs_login(self, alias):
        return self._add(
            FakeKasaDevice(
                alias=alias, encryption=DeviceEncryptionType.Klap, credentials=("u", "p")
            )
        )

    def go_offline(self, options):
        self.discover.devices[options["host"]].unreachable = True

    def live(self, options):
        d = self.discover.devices[options["host"]]
        return [c.on for c in d.children] if d.children else d.on


class TestKasaBackend(BackendContract):
    @pytest.fixture
    def rig(self) -> KasaRig:
        return KasaRig()


class TestFakeBackend(BackendContract):
    @pytest.fixture
    def rig(self) -> FakeRig:
        return FakeRig()
