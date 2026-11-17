"""TP-Link Kasa over the local network (python-kasa). The only module that imports python-kasa.

Older firmware speaks the legacy local protocol (XOR) and needs no login; newer firmware uses
KLAP (or AES) and needs the TP-Link account's username/password for the *local* handshake.
Either way nothing goes through TP-Link's cloud. See docs/kasa.md.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import kasa
from kasa import Credentials, DeviceEncryptionType, Discover

from coco_hub.devices.errors import AuthRequired, DeviceError, Unreachable, Unsupported

DiscoverSingle = Callable[..., Awaitable[Any]]
"""``kasa.Discover.discover_single`` or a test fake with the same keyword arguments."""

_PROTOCOLS = {
    DeviceEncryptionType.Xor: ("legacy (XOR)", False),
    DeviceEncryptionType.Klap: ("KLAP", True),
    DeviceEncryptionType.Aes: ("AES", True),
}


@dataclass(frozen=True)
class Outlet:
    index: int
    alias: str
    is_on: bool


@dataclass(frozen=True)
class KasaInfo:
    alias: str
    model: str
    hw_ver: str
    fw_ver: str
    mac: str
    protocol: str
    needs_credentials: bool
    is_on: bool
    outlets: tuple[Outlet, ...]


def translate(exc: BaseException) -> DeviceError:
    """python-kasa's exceptions as the Hub's typed device errors."""
    if isinstance(exc, DeviceError):
        return exc
    if isinstance(exc, kasa.AuthenticationError):
        return AuthRequired("the device rejected or needs the TP-Link login")
    if isinstance(exc, kasa.UnsupportedDeviceError):
        return Unsupported(f"python-kasa doesn't support this device: {exc}")
    if isinstance(exc, (kasa.KasaException, OSError, asyncio.TimeoutError)):
        return Unreachable(f"{type(exc).__name__}: {exc}")
    return DeviceError(f"{type(exc).__name__}: {exc}")


async def connect(
    host: str,
    *,
    credentials: tuple[str, str] | None = None,
    timeout_s: float = 2.0,
    discover: DiscoverSingle | None = None,
) -> Any:
    """Find the device at ``host`` (one unicast query, not a network scan) and read its state.

    Raises ``Unreachable`` / ``AuthRequired`` / ``Unsupported``.
    """
    discover = discover or Discover.discover_single
    seconds = max(1, math.ceil(timeout_s))
    kwargs: dict[str, Any] = {"discovery_timeout": seconds, "timeout": seconds}
    if credentials is not None:
        kwargs["credentials"] = Credentials(*credentials)
    try:
        device = await discover(host, **kwargs)
        if device is None:
            raise Unreachable(f"no Kasa device answered at {host}")
        await device.update()
    except Exception as exc:
        raise translate(exc) from exc
    return device


def protocol_of(device: Any) -> tuple[str, bool]:
    """(label, needs_credentials) from the connection the device negotiated."""
    encryption = device.config.connection_type.encryption_type
    return _PROTOCOLS.get(encryption, (str(encryption), True))


def describe(device: Any) -> KasaInfo:
    """What the device says about itself, from its last ``update()``."""
    protocol, needs_credentials = protocol_of(device)
    hw = device.hw_info or {}
    return KasaInfo(
        alias=device.alias or "",
        model=device.model or "",
        hw_ver=str(hw.get("hw_ver", "")),
        fw_ver=str(hw.get("sw_ver", "")),
        mac=device.mac or "",
        protocol=protocol,
        needs_credentials=needs_credentials,
        is_on=bool(device.is_on),
        outlets=tuple(
            Outlet(index=i, alias=c.alias or "", is_on=bool(c.is_on))
            for i, c in enumerate(device.children or ())
        ),
    )


def find_child(device: Any, child: str | int) -> Any:
    """An outlet by 0-based index or alias (case-insensitive). Raises ``ValueError``."""
    children = list(device.children or ())
    if isinstance(child, int) or (isinstance(child, str) and child.isdigit()):
        index = int(child)
        if 0 <= index < len(children):
            return children[index]
    else:
        for c in children:
            if (c.alias or "").casefold() == child.strip().casefold():
                return c
    names = ", ".join(f"{i}: {c.alias!r}" for i, c in enumerate(children)) or "none"
    raise ValueError(f"no outlet {child!r} on this device (outlets: {names})")


async def disconnect(device: Any) -> None:
    try:
        await device.disconnect()
    except Exception:  # noqa: BLE001 - closing a connection must never fail the caller
        pass
