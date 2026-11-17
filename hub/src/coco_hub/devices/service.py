"""DeviceService: configured devices by spoken name, over any backend (#133).

Intents call ``state(name)`` / ``set_power(name, on)`` / ``capabilities(name)`` with a spoken
name; the service resolves it (config ``name``, ``aliases``, else the device's own alias),
connects lazily, applies the backend's per-call timeout (``[backends.<name>] timeout_s``,
default 2 s), and raises only ``devices.errors`` types. A device failure only ever fails the
request that touched it: nothing here blocks the event loop or other devices.

Room left on purpose, not built: a name could resolve to several devices (groups/rooms,
"turn off all the lights"), and devices of several backends already coexist (one backend
instance per name in ``[backends]``).
"""

import asyncio
import logging
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from coco_hub.devices.backend import DeviceBackend, DeviceHandle
from coco_hub.devices.backends import backend_class
from coco_hub.devices.errors import DeviceError, UnknownDevice, Unreachable, Unsupported
from coco_hub.devices.model import Capability, DeviceState, PowerState
from coco_hub.intents.normalize import normalize
from coco_hub.settings import BackendSettings, DeviceConfig, Settings

log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_RETRY_S = 60.0


def spoken_key(name: str) -> str:
    """A name as an utterance would contain it after ``normalize`` (minus a leading "the")."""
    key = normalize(name)
    return key[4:] if key.startswith("the ") else key


@dataclass
class _Device:
    index: int
    config: DeviceConfig
    backend: DeviceBackend
    options: Any
    timeout_s: float
    handle: DeviceHandle | None = None
    alias: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def display_name(self) -> str | None:
        return self.config.name or self.alias

    @property
    def label(self) -> str:
        """For logs: never options (they may hold addresses), never secrets."""
        return f"device #{self.index} ({self.config.backend})"


@dataclass(frozen=True)
class PowerChange:
    name: str
    target: bool
    before: DeviceState
    after: DeviceState

    @property
    def changed(self) -> bool:
        """A command was sent (the device wasn't already in the target state)."""
        return self.before.power != _power(self.target)

    @property
    def confirmed(self) -> bool:
        """The device reported the target state when read back."""
        return self.after.power == _power(self.target)


def _power(on: bool) -> PowerState:
    return PowerState.ON if on else PowerState.OFF


class DeviceService:
    def __init__(
        self,
        devices: Sequence[DeviceConfig],
        backends: Mapping[str, DeviceBackend],
        backend_settings: Mapping[str, BackendSettings] | None = None,
        *,
        retry_s: float = DEFAULT_RETRY_S,
    ) -> None:
        """Raises ``ValueError`` for an unknown backend, bad options or a duplicate name."""
        backend_settings = backend_settings or {}
        self._backends = dict(backends)
        self._retry_s = retry_s
        self._devices: list[_Device] = []
        for i, cfg in enumerate(devices):
            backend = self._backends.get(cfg.backend)
            if backend is None:
                raise ValueError(f"device {cfg.name or i}: unknown backend {cfg.backend!r}")
            try:
                options = backend.parse_options(cfg.options)
            except ValueError as exc:
                raise ValueError(
                    f"device {cfg.name or f'#{i}'}: bad options for {cfg.backend}: {exc}"
                ) from exc
            timeout = backend_settings.get(cfg.backend, BackendSettings()).timeout_s
            self._devices.append(_Device(i, cfg, backend, options, timeout))
        self._names: dict[str, _Device] = {}
        self._rebuild_names(strict=True)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        backends: Mapping[str, DeviceBackend] | None = None,
        retry_s: float = DEFAULT_RETRY_S,
    ) -> "DeviceService":
        """Build a backend for each backend a configured device uses. ``backends`` (tests)
        supplies instances instead."""
        built: dict[str, DeviceBackend] = dict(backends or {})
        for cfg in settings.devices:
            if cfg.backend not in built:
                bs = settings.backends.get(cfg.backend, BackendSettings())
                built[cfg.backend] = backend_class(cfg.backend)(bs)
        return cls(settings.devices, built, settings.backends, retry_s=retry_s)

    # --- names ---------------------------------------------------------------------------

    def _rebuild_names(self, *, strict: bool = False) -> None:
        names: dict[str, _Device] = {}
        for dev in self._devices:
            spoken = [dev.display_name, *dev.config.aliases]
            for name in spoken:
                if not name:
                    continue
                key = spoken_key(name)
                if not key:
                    continue
                other = names.get(key)
                if other is None:
                    names[key] = dev
                elif other is not dev:
                    if strict:
                        raise ValueError(
                            f"two devices are both called {key!r}: give one a different name"
                        )
                    log.warning(
                        "devices: %s and %s are both called %r; using the first",
                        other.label,
                        dev.label,
                        key,
                    )
        self._names = names

    def spoken_names(self) -> dict[str, str]:
        """Every name a device answers to (normalised) -> the name Coco says back."""
        return {key: dev.display_name or key for key, dev in self._names.items()}

    def _resolve(self, name: str) -> _Device:
        dev = self._names.get(spoken_key(name))
        if dev is None:
            raise UnknownDevice(f"no device called {name!r}")
        return dev

    def display_name(self, name: str) -> str:
        dev = self._resolve(name)
        return dev.display_name or name

    # --- device calls --------------------------------------------------------------------

    async def _timed(self, dev: _Device, awaitable: Awaitable[T]) -> T:
        try:
            return await asyncio.wait_for(awaitable, dev.timeout_s)
        except TimeoutError as exc:
            raise Unreachable(f"no answer within {dev.timeout_s:g} s") from exc

    async def _handle(self, dev: _Device) -> DeviceHandle:
        if dev.handle is None:
            handle = await self._timed(dev, dev.backend.connect(dev.options))
            dev.handle = handle
            if handle.alias and handle.alias != dev.alias:
                dev.alias = handle.alias
                self._rebuild_names()
        return dev.handle

    async def _drop(self, dev: _Device) -> None:
        handle, dev.handle = dev.handle, None
        if handle is not None:
            try:
                await handle.close()
            except Exception:  # noqa: BLE001 - a broken connection may fail to close
                log.debug("devices: closing %s failed", dev.label, exc_info=True)

    async def _call(self, dev: _Device, op: str, fn) -> Any:
        """Run ``fn(handle)`` under the device's lock (``fn`` times each device call itself:
        the limit is per call, not per request). On failure the connection is dropped so the
        next call reconnects (the device may have rebooted or moved)."""
        async with dev.lock:
            try:
                handle = await self._handle(dev)
                return await fn(handle)
            except DeviceError as exc:
                log.info("devices: %s %s failed: %s", dev.label, op, type(exc).__name__)
                if not isinstance(exc, Unsupported):
                    await self._drop(dev)
                raise

    async def capabilities(self, name: str) -> frozenset[Capability]:
        dev = self._resolve(name)
        async with dev.lock:
            handle = await self._handle(dev)
        return handle.capabilities

    async def state(self, name: str) -> DeviceState:
        dev = self._resolve(name)
        return await self._call(dev, "state", lambda h: self._timed(dev, h.get_state()))

    async def set_power(self, name: str, on: bool) -> PowerChange:
        """Read, switch only if needed, read back. Never claims what it didn't read."""
        dev = self._resolve(name)

        async def run(handle: DeviceHandle) -> PowerChange:
            if Capability.POWER not in handle.capabilities:
                raise Unsupported(f"{dev.label} has no power switch")
            before = await self._timed(dev, handle.get_state())
            after = before
            if before.power != _power(on):
                await self._timed(dev, handle.set_power(on))
                after = await self._timed(dev, handle.get_state())
            return PowerChange(dev.display_name or name, on, before, after)

        return await self._call(dev, "set_power", run)

    # --- lifecycle -----------------------------------------------------------------------

    async def _try_connect(self, dev: _Device) -> None:
        async with dev.lock:
            if dev.handle is not None:
                return
            try:
                await self._handle(dev)
            except DeviceError as exc:
                log.warning(
                    "devices: %s not connected yet (%s)%s",
                    dev.label,
                    type(exc).__name__,
                    "" if dev.display_name else "; it has no name until it answers",
                )

    async def start(self) -> None:
        """Connect every device at once (bounded by each timeout) to learn device aliases.
        Failures are logged; named devices connect again on first use."""
        await asyncio.gather(*(self._try_connect(d) for d in self._devices))
        log.info(
            "devices: %d configured, %d connected",
            len(self._devices),
            sum(d.handle is not None for d in self._devices),
        )

    async def run(self) -> None:
        """Background task: keep trying devices that have never answered (an unnamed device
        has no spoken name until it does)."""
        while True:
            pending = [d for d in self._devices if d.alias is None]
            if not pending:
                return
            await asyncio.sleep(self._retry_s)
            await asyncio.gather(*(self._try_connect(d) for d in pending))

    async def serve(self) -> None:
        """``start`` then ``run``: the app's background task."""
        await self.start()
        await self.run()

    async def aclose(self) -> None:
        for dev in self._devices:
            await self._drop(dev)
        for backend in self._backends.values():
            try:
                await backend.aclose()
            except Exception:  # noqa: BLE001
                log.debug("devices: closing backend %s failed", backend.name, exc_info=True)
