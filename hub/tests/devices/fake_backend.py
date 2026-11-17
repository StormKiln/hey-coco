"""An in-memory ``DeviceBackend`` for service and intent tests (and its contract rig).

Devices are addressed by ``options = {"id": "<id>"}``; ``child`` picks an outlet. Fictional.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from coco_hub.devices.backend import DeviceBackend, DeviceHandle
from coco_hub.devices.errors import AuthRequired, UnknownDevice, Unreachable
from coco_hub.devices.model import Capability, DeviceState, OutletState, PowerState


@dataclass
class FakeDevice:
    alias: str
    on: bool = False
    outlets: list[list] = field(default_factory=list)  # [[alias, on], ...]
    stuck: bool = False
    offline: bool = False
    needs_login: bool = False
    delay_s: float = 0.0
    capabilities: frozenset[Capability] = frozenset({Capability.POWER})
    commands: list[bool] = field(default_factory=list)
    connects: int = 0


class FakeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    child: int | str | None = None


class FakeHandle(DeviceHandle):
    def __init__(self, device: FakeDevice, outlet: int | None) -> None:
        self.device = device
        self.outlet = outlet
        self.alias = device.outlets[outlet][0] if outlet is not None else device.alias
        self.capabilities = device.capabilities
        self.closed = 0

    async def _ready(self) -> None:
        if self.device.delay_s:
            await asyncio.sleep(self.device.delay_s)
        if self.device.offline:
            raise Unreachable("fake offline")

    async def get_state(self) -> DeviceState:
        await self._ready()
        d = self.device
        if self.outlet is not None:
            return DeviceState.of(d.outlets[self.outlet][1])
        if d.outlets:
            return DeviceState.from_outlets([OutletState(a, on) for a, on in d.outlets])
        return DeviceState(PowerState.ON if d.on else PowerState.OFF)

    async def set_power(self, on: bool) -> None:
        await self._ready()
        d = self.device
        d.commands.append(on)
        if d.stuck:
            return
        if self.outlet is not None:
            d.outlets[self.outlet][1] = on
        elif d.outlets:
            for o in d.outlets:
                o[1] = on
        else:
            d.on = on

    async def close(self) -> None:
        self.closed += 1


class FakeBackend(DeviceBackend):
    name = "fake"

    def __init__(self, devices: Mapping[str, FakeDevice] | None = None, **_: Any) -> None:
        self.devices: dict[str, FakeDevice] = dict(devices or {})
        self.closed = False

    def parse_options(self, options: Mapping[str, Any]) -> FakeOptions:
        return FakeOptions.model_validate(dict(options))

    async def connect(self, options: FakeOptions) -> FakeHandle:
        device = self.devices.get(options.id)
        if device is None or device.offline:
            raise Unreachable(f"nothing at {options.id}")
        device.connects += 1
        if device.delay_s:
            await asyncio.sleep(device.delay_s)
        if device.needs_login:
            raise AuthRequired("fake login")
        outlet = None
        if options.child is not None:
            names = [a.casefold() for a, _ in device.outlets]
            if isinstance(options.child, int) and 0 <= options.child < len(names):
                outlet = options.child
            elif isinstance(options.child, str) and options.child.casefold() in names:
                outlet = names.index(options.child.casefold())
            else:
                raise UnknownDevice(f"no outlet {options.child!r}")
        return FakeHandle(device, outlet)

    async def aclose(self) -> None:
        self.closed = True


class FakeRig:
    supports_outlets = True

    def __init__(self) -> None:
        self._backend = FakeBackend()
        self._n = 0

    def backend(self) -> FakeBackend:
        return self._backend

    def _add(self, device: FakeDevice, child: Any = None) -> dict:
        self._n += 1
        key = f"dev{self._n}"
        self._backend.devices[key] = device
        return {"id": key} if child is None else {"id": key, "child": child}

    def device(self, alias, *, on=False, stuck=False):
        return self._add(FakeDevice(alias, on=on, stuck=stuck))

    def outlets(self, alias, outlets, child=None):
        return self._add(FakeDevice(alias, outlets=[[a, on] for a, on in outlets]), child)

    def missing(self):
        return {"id": "nowhere"}

    def needs_login(self, alias):
        return self._add(FakeDevice(alias, needs_login=True))

    def go_offline(self, options):
        self._backend.devices[options["id"]].offline = True

    def live(self, options):
        d = self._backend.devices[options["id"]]
        return [on for _, on in d.outlets] if d.outlets else d.on
