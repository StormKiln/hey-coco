"""The DeviceService in the running app: registered, never delays startup, closed on exit."""

import time

import pytest
from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.devices.service import DeviceService
from coco_hub.settings import DeviceConfig

from fake_backend import FakeBackend, FakeDevice


def with_devices(settings, *devices: dict):
    return settings.model_copy(update={"devices": [DeviceConfig(**d) for d in devices]})


def test_app_registers_the_device_service(settings):
    backend = FakeBackend({"a": FakeDevice("Hall Light")})
    s = with_devices(settings, {"backend": "fake", "options": {"id": "a"}})
    with TestClient(create_app(s, device_backends={"fake": backend})) as client:
        svc = client.app.state.services["devices"]
        assert isinstance(svc, DeviceService)
        for _ in range(100):  # connects in the background
            if svc.spoken_names():
                break
            time.sleep(0.01)
        assert svc.spoken_names() == {"hall light": "Hall Light"}
    assert backend.closed


def test_slow_devices_do_not_delay_startup(settings):
    backend = FakeBackend({"a": FakeDevice("Plug", delay_s=1.5)})
    s = with_devices(settings, {"name": "fan", "backend": "fake", "options": {"id": "a"}})
    start = time.monotonic()
    with TestClient(create_app(s, device_backends={"fake": backend})) as client:
        assert client.get("/healthz").status_code == 200
        assert time.monotonic() - start < 1.0


def test_bad_device_config_fails_at_startup(settings):
    s = with_devices(settings, {"name": "fan", "backend": "kasa", "options": {}})
    with pytest.raises(ValueError, match="fan"):
        create_app(s)


def test_no_devices_is_fine(settings):
    with TestClient(create_app(settings)) as client:
        assert client.app.state.services["devices"].spoken_names() == {}
