"""A fake python-kasa device and ``Discover.discover_single`` for tests (no network).

Mirrors the parts of python-kasa's ``Device`` the Hub uses: ``alias``, ``model``, ``mac``,
``hw_info``, ``children``, ``is_on``, ``config.connection_type.encryption_type``,
``update()``, ``turn_on()``, ``turn_off()``, ``disconnect()``. Like the real library,
``is_on`` is the state read by the last ``update()``, not the live state, so code that
forgets to re-read is caught. Names and addresses are fictional.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace

import kasa
from kasa import DeviceEncryptionType


@dataclass(eq=False)
class FakeKasaDevice:
    alias: str = "Desk lamp"
    model: str = "XX100(US)"
    on: bool = False
    """The live relay state (what the hardware is doing)."""
    encryption: DeviceEncryptionType = DeviceEncryptionType.Xor
    credentials: tuple[str, str] | None = None
    """KLAP/AES devices: the TP-Link login the local handshake needs."""
    hw_ver: str = "1.0"
    sw_ver: str = "1.0.0 Build 000000 Rel.000000"
    mac: str = "00:00:5E:00:53:01"
    unreachable: bool = False
    stuck: bool = False
    """Ignores on/off commands (to test that the Hub never claims a change it didn't see)."""
    children: list["FakeKasaDevice"] = field(default_factory=list)
    parent: "FakeKasaDevice | None" = None
    updates: int = 0
    commands: list[tuple[str, bool]] = field(default_factory=list)
    disconnected: bool = False
    given_credentials: tuple[str, str] | None = None
    _seen: bool | None = None

    def __post_init__(self) -> None:
        for child in self.children:
            child.parent = self
        self.config = SimpleNamespace(
            connection_type=SimpleNamespace(encryption_type=self.encryption)
        )

    @property
    def hw_info(self) -> dict[str, str]:
        return {"hw_ver": self.hw_ver, "sw_ver": self.sw_ver}

    @property
    def is_on(self) -> bool:
        if self._seen is None:
            raise kasa.KasaException("update() has not been called")
        return self._seen

    def _live(self) -> bool:
        if self.children:
            return any(c.on for c in self.children)
        return self.on

    def _root(self) -> "FakeKasaDevice":
        return self.parent._root() if self.parent else self

    def _check_reachable(self) -> None:
        root = self._root()
        if root.unreachable:
            raise kasa.TimeoutError("fake: no answer")
        if root.credentials is not None and root.given_credentials != root.credentials:
            raise kasa.AuthenticationError("fake: login required")

    async def update(self) -> None:
        root = self._root()
        root._check_reachable()
        root.updates += 1
        root._seen = root._live()
        for child in root.children:
            child._seen = child.on

    async def _set(self, on: bool) -> None:
        root = self._root()
        root._check_reachable()
        root.commands.append((self.alias, on))
        if root.stuck:
            return
        if self.children:
            for child in self.children:
                child.on = on
        else:
            self.on = on

    async def turn_on(self) -> None:
        await self._set(True)

    async def turn_off(self) -> None:
        await self._set(False)

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeDiscover:
    """Stands in for ``kasa.Discover.discover_single``: one fake device per host."""

    def __init__(self, devices: dict[str, FakeKasaDevice] | None = None) -> None:
        self.devices = devices or {}
        self.calls: list[dict] = []

    async def __call__(self, host: str, **kwargs) -> FakeKasaDevice:
        self.calls.append({"host": host, **kwargs})
        device = self.devices.get(host)
        if device is None or device.unreachable:
            raise kasa.TimeoutError(f"fake: nothing at {host}")
        creds = kwargs.get("credentials")
        if creds is not None:
            device.given_credentials = (creds.username, creds.password)
        return device


def strip(*outlets: tuple[str, bool], **kwargs) -> FakeKasaDevice:
    """A multi-outlet fake: ``strip(("Fan", True), ("Kettle", False))``."""
    kwargs.setdefault("alias", "Power strip")
    return FakeKasaDevice(
        children=[FakeKasaDevice(alias=a, on=on) for a, on in outlets],
        **kwargs,
    )
