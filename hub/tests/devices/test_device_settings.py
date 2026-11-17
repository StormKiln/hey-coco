"""#133: ``[[devices]]`` and ``[backends.<name>]`` settings (fictional devices only)."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from coco_hub.settings import Settings

from conftest import TEST_TOKEN

TOML = """
token = "from-file-token-0123456789abc"

[backends.kasa]
username = "someone@example.test"
password = "hunter2-not-real"

[[devices]]
name = "desk lamp"
aliases = ["lamp", "reading light"]
backend = "kasa"
options = { host = "192.0.2.10" }

[[devices]]
backend = "kasa"
[devices.options]
host = "192.0.2.11"
child = 1
mac = "00:00:5E:00:53:02"
"""


def load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str = TOML) -> Settings:
    path = tmp_path / "config.local.toml"
    path.write_text(body)
    monkeypatch.setenv("COCO_CONFIG", str(path))
    return Settings()  # type: ignore[call-arg]


def test_no_devices_by_default():
    s = Settings(token=TEST_TOKEN)
    assert s.devices == []
    assert s.backends == {}


def test_devices_and_backends_from_toml(tmp_path, monkeypatch):
    s = load(tmp_path, monkeypatch)
    lamp, outlet = s.devices
    assert lamp.name == "desk lamp"
    assert lamp.aliases == ["lamp", "reading light"]
    assert lamp.backend == "kasa"
    assert lamp.options == {"host": "192.0.2.10"}
    assert outlet.name is None  # spoken name comes from the device's own alias
    assert outlet.options == {"host": "192.0.2.11", "child": 1, "mac": "00:00:5E:00:53:02"}
    kasa = s.backends["kasa"]
    assert kasa.username == "someone@example.test"
    assert isinstance(kasa.password, SecretStr)
    assert kasa.timeout_s == 2.0


def test_backend_secrets_never_show_in_repr(tmp_path, monkeypatch):
    s = load(tmp_path, monkeypatch)
    assert "hunter2" not in repr(s)
    assert "hunter2" not in str(s.backends)


def test_password_from_env(monkeypatch):
    monkeypatch.setenv("COCO_BACKENDS__KASA__PASSWORD", "env-secret")
    s = Settings(token=TEST_TOKEN)
    assert s.backends["kasa"].password.get_secret_value() == "env-secret"


def test_backend_is_required():
    with pytest.raises(ValidationError, match="backend"):
        Settings(token=TEST_TOKEN, devices=[{"name": "fan", "options": {}}])


def test_blank_name_is_rejected():
    with pytest.raises(ValidationError, match="name"):
        Settings(token=TEST_TOKEN, devices=[{"name": "  ", "backend": "kasa"}])


def test_timeout_bounds():
    with pytest.raises(ValidationError):
        Settings(token=TEST_TOKEN, backends={"kasa": {"timeout_s": 0}})
