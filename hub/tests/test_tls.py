"""The Hub serves HTTPS/WSS with its own certificate (#54).

These start a real uvicorn server on a loopback port (no LAN, no internet).
"""

import hashlib
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from coco_hub import __main__ as hub_main
from coco_hub import identity
from coco_hub.app import create_app
from coco_hub.settings import Settings, Tls

from conftest import TEST_TOKEN


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def peer_cert_der(port: int) -> bytes:
    """The certificate the server presents, without trusting it."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            der = tls.getpeercert(binary_form=True)
    assert der is not None
    return der


@pytest.fixture
def tls_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"host": "127.0.0.1", "port": free_port()})


@pytest.fixture
def running_hub(tls_settings: Settings) -> Iterator[Settings]:
    config = uvicorn.Config(
        create_app(tls_settings),
        log_config=None,
        **hub_main.server_options(tls_settings),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "Hub did not start"
        time.sleep(0.02)
    try:
        yield tls_settings
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_server_options_use_the_identity_files(tls_settings: Settings):
    options = hub_main.server_options(tls_settings)
    ident = identity.load_or_create(tls_settings.data_dir)
    assert options["ssl_certfile"] == str(ident.cert_path)
    assert options["ssl_keyfile"] == str(ident.key_path)
    assert options["host"] == "127.0.0.1"
    assert options["port"] == tls_settings.port


def test_server_options_without_tls_have_no_ssl(tls_settings: Settings):
    plain = tls_settings.model_copy(update={"tls": Tls(enabled=False)})
    options = hub_main.server_options(plain)
    assert "ssl_certfile" not in options and "ssl_keyfile" not in options


def test_hub_serves_https_with_its_pinned_certificate(running_hub: Settings):
    ident = identity.load_or_create(running_hub.data_dir)
    der = peer_cert_der(running_hub.port)
    assert der == ident.der
    assert hashlib.sha256(der).hexdigest() == ident.fingerprint

    base = f"https://127.0.0.1:{running_hub.port}"
    with httpx.Client(verify=False) as client:
        health = client.get(f"{base}/healthz").json()
        ping = client.get(f"{base}/v1/ping", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
    assert health == {
        "status": "ok",
        "hub_id": ident.hub_id,
        "fingerprint": ident.short_fingerprint,
    }
    assert ping.json() == {"pong": True}


def test_hub_trusts_nothing_but_its_own_certificate_as_ca(running_hub: Settings):
    """A client that pins the Hub's cert as its only CA verifies the chain (loopback SAN)."""
    ident = identity.load_or_create(running_hub.data_dir)
    context = ssl.create_default_context(cafile=str(ident.cert_path))
    with httpx.Client(verify=context) as client:
        response = client.get(f"https://127.0.0.1:{running_hub.port}/healthz")
    assert response.status_code == 200


def test_plain_http_is_not_served(running_hub: Settings):
    with httpx.Client() as client, pytest.raises(httpx.HTTPError):
        client.get(
            f"http://127.0.0.1:{running_hub.port}/v1/ping",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )


def test_fingerprint_command_prints_the_short_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("COCO_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("COCO_DATA_DIR", str(tmp_path / "data"))
    assert hub_main.main(["--fingerprint"]) == 0
    ident = identity.load_or_create(tmp_path / "data")
    out = capsys.readouterr().out
    assert ident.short_fingerprint in out
    assert ident.hub_id in out
    assert TEST_TOKEN not in out
