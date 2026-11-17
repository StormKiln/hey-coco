"""TP-Link Kasa over the local network (python-kasa). The only module that imports python-kasa.

Older firmware speaks the legacy local protocol (XOR) and needs no login; newer firmware uses
KLAP (or AES) and needs the TP-Link account's username/password for the *local* handshake.
Either way nothing goes through TP-Link's cloud. See docs/kasa.md.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import kasa
from kasa import Credentials, DeviceEncryptionType, Discover
from pydantic import BaseModel, ConfigDict, field_validator

from coco_hub.devices.backend import DeviceBackend, DeviceHandle
from coco_hub.devices.errors import (
    AuthRequired,
    DeviceError,
    UnknownDevice,
    Unreachable,
    Unsupported,
)
from coco_hub.devices.model import Capability, DeviceState, OutletState
from coco_hub.settings import BackendSettings

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


# --- DeviceBackend ------------------------------------------------------------------------


class KasaOptions(BaseModel):
    """``options`` of a ``backend = "kasa"`` device."""

    model_config = ConfigDict(extra="forbid")

    host: str
    """IP address or hostname. Give the plug a DHCP reservation so it doesn't move."""
    mac: str | None = None
    """If set, refuse to control a different device that has taken over ``host``."""
    child: int | str | None = None
    """One outlet of a multi-outlet plug or strip: 0-based index or the outlet's alias.
    Unset: the whole device (every outlet)."""

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("host must not be blank")
        return value.strip()


def _mac_key(mac: str) -> str:
    return "".join(ch for ch in mac.lower() if ch in "0123456789abcdef")


class KasaHandle(DeviceHandle):
    def __init__(self, device: Any, target: Any) -> None:
        self._device = device
        self._target = target
        self.alias = target.alias or device.alias or ""
        self.capabilities = frozenset({Capability.POWER})
        self._closed = False

    async def _refresh(self) -> None:
        try:
            await self._device.update()
        except Exception as exc:
            raise translate(exc) from exc

    async def get_state(self) -> DeviceState:
        await self._refresh()
        target = self._target
        if target.children:
            return DeviceState.from_outlets(
                [
                    OutletState(c.alias or f"outlet {i}", bool(c.is_on))
                    for i, c in enumerate(target.children)
                ]
            )
        return DeviceState.of(bool(target.is_on))

    async def set_power(self, on: bool) -> None:
        try:
            await (self._target.turn_on() if on else self._target.turn_off())
        except Exception as exc:
            raise translate(exc) from exc

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await disconnect(self._device)


class KasaBackend(DeviceBackend):
    name = "kasa"

    def __init__(
        self, settings: BackendSettings | None = None, *, discover: DiscoverSingle | None = None
    ) -> None:
        super().__init__(settings)
        self._discover = discover

    def _credentials(self) -> tuple[str, str] | None:
        s = self.settings
        if s.username and s.password is not None:
            return (s.username, s.password.get_secret_value())
        return None

    def parse_options(self, options: Mapping[str, Any]) -> KasaOptions:
        return KasaOptions.model_validate(dict(options))

    async def connect(self, options: KasaOptions) -> KasaHandle:
        # Legacy (XOR) devices never need the login, so try without it first and only
        # send credentials to a device that asks (KLAP/AES).
        try:
            device = await connect(
                options.host, timeout_s=self.settings.timeout_s, discover=self._discover
            )
        except AuthRequired:
            if (creds := self._credentials()) is None:
                raise
            device = await connect(
                options.host,
                credentials=creds,
                timeout_s=self.settings.timeout_s,
                discover=self._discover,
            )
        if options.mac and _mac_key(options.mac) != _mac_key(device.mac or ""):
            await disconnect(device)
            raise Unreachable(
                f"a different device is at {options.host} (MAC doesn't match the config)"
            )
        target = device
        if options.child is not None:
            try:
                target = find_child(device, options.child)
            except ValueError as exc:
                await disconnect(device)
                raise UnknownDevice(str(exc)) from exc
        return KasaHandle(device, target)
