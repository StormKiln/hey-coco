"""The contract every ``DeviceBackend`` passes, against that backend's fake device.

A new backend gets this coverage by writing a ``Rig`` (how to set up its fake devices) and
one test class::

    class TestShellyBackend(BackendContract):
        @pytest.fixture
        def rig(self):
            return ShellyRig()

Outlet tests are skipped for a rig with ``supports_outlets = False``.
"""

from collections.abc import Mapping
from typing import Any, Protocol

import pytest

from coco_hub.devices.backend import DeviceBackend, DeviceHandle
from coco_hub.devices.errors import AuthRequired, UnknownDevice, Unreachable
from coco_hub.devices.model import Capability, PowerState

Options = Mapping[str, Any]


class Rig(Protocol):
    supports_outlets: bool

    def backend(self) -> DeviceBackend: ...

    def device(self, alias: str, *, on: bool = False, stuck: bool = False) -> Options:
        """A reachable single-relay fake; returns the ``options`` that address it."""

    def outlets(self, alias: str, outlets: list[tuple[str, bool]], child: Any = None) -> Options:
        """A multi-outlet fake. ``child`` (index or alias) addresses one outlet."""

    def missing(self) -> Options:
        """Options for an address where nothing answers."""

    def needs_login(self, alias: str) -> Options:
        """A device that needs credentials the backend wasn't given."""

    def go_offline(self, options: Options) -> None:
        """The fake stops answering (after it was connected)."""

    def live(self, options: Options) -> bool | list[bool]:
        """What the fake hardware is really doing (per outlet for a multi-outlet device)."""


class BackendContract:
    @pytest.fixture
    def rig(self) -> Rig:
        raise NotImplementedError

    async def connect(self, rig: Rig, options: Options) -> DeviceHandle:
        backend = rig.backend()
        return await backend.connect(backend.parse_options(options))

    # --- connecting ----------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_connect_reports_alias_and_power_capability(self, rig):
        handle = await self.connect(rig, rig.device("Desk lamp"))
        assert handle.alias == "Desk lamp"
        assert Capability.POWER in handle.capabilities

    @pytest.mark.anyio
    async def test_nothing_at_address_is_unreachable(self, rig):
        with pytest.raises(Unreachable):
            await self.connect(rig, rig.missing())

    @pytest.mark.anyio
    async def test_missing_login_is_auth_required(self, rig):
        with pytest.raises(AuthRequired):
            await self.connect(rig, rig.needs_login("Fan"))

    def test_bad_options_fail_at_parse_time(self, rig):
        with pytest.raises(ValueError):
            rig.backend().parse_options({"definitely_not_an_option": True})

    # --- state and power -----------------------------------------------------------------

    @pytest.mark.anyio
    @pytest.mark.parametrize("on", [True, False])
    async def test_state_reads_the_device(self, rig, on):
        handle = await self.connect(rig, rig.device("Fan", on=on))
        state = await handle.get_state()
        assert state.power == (PowerState.ON if on else PowerState.OFF)

    @pytest.mark.anyio
    @pytest.mark.parametrize("on", [True, False])
    async def test_set_power_changes_the_hardware(self, rig, on):
        options = rig.device("Fan", on=not on)
        handle = await self.connect(rig, options)
        await handle.set_power(on)
        assert rig.live(options) is on
        assert (await handle.get_state()).power == (PowerState.ON if on else PowerState.OFF)

    @pytest.mark.anyio
    async def test_state_is_read_fresh_not_cached(self, rig):
        options = rig.device("Fan", on=False)
        handle = await self.connect(rig, options)
        assert (await handle.get_state()).power == PowerState.OFF
        await handle.set_power(True)
        assert (await handle.get_state()).power == PowerState.ON

    @pytest.mark.anyio
    async def test_device_that_ignores_commands_reads_back_unchanged(self, rig):
        handle = await self.connect(rig, rig.device("Fan", on=False, stuck=True))
        await handle.set_power(True)
        assert (await handle.get_state()).power == PowerState.OFF

    @pytest.mark.anyio
    async def test_going_offline_is_unreachable(self, rig):
        options = rig.device("Fan")
        handle = await self.connect(rig, options)
        rig.go_offline(options)
        with pytest.raises(Unreachable):
            await handle.get_state()
        with pytest.raises(Unreachable):
            await handle.set_power(True)

    @pytest.mark.anyio
    async def test_close_is_idempotent(self, rig):
        handle = await self.connect(rig, rig.device("Fan"))
        await handle.close()
        await handle.close()

    # --- multi-outlet devices ------------------------------------------------------------

    @pytest.fixture
    def outlets_rig(self, rig):
        if not rig.supports_outlets:
            pytest.skip("backend has no multi-outlet devices")
        return rig

    @pytest.mark.anyio
    async def test_whole_device_with_mixed_outlets(self, outlets_rig):
        rig = outlets_rig
        handle = await self.connect(rig, rig.outlets("Strip", [("Fan", True), ("Kettle", False)]))
        state = await handle.get_state()
        assert state.power == PowerState.MIXED
        assert [(o.name, o.on) for o in state.outlets] == [("Fan", True), ("Kettle", False)]

    @pytest.mark.anyio
    async def test_whole_device_switches_every_outlet(self, outlets_rig):
        rig = outlets_rig
        options = rig.outlets("Strip", [("Fan", True), ("Kettle", False)])
        handle = await self.connect(rig, options)
        await handle.set_power(True)
        assert rig.live(options) == [True, True]

    @pytest.mark.anyio
    async def test_one_outlet_by_index(self, outlets_rig):
        rig = outlets_rig
        options = rig.outlets("Strip", [("Fan", False), ("Kettle", False)], child=1)
        handle = await self.connect(rig, options)
        assert handle.alias == "Kettle"
        await handle.set_power(True)
        assert rig.live(options) == [False, True]
        assert (await handle.get_state()).power == PowerState.ON

    @pytest.mark.anyio
    async def test_one_outlet_by_alias(self, outlets_rig):
        rig = outlets_rig
        options = rig.outlets("Strip", [("Fan", False), ("Kettle", True)], child="fan")
        handle = await self.connect(rig, options)
        assert handle.alias == "Fan"
        assert (await handle.get_state()).power == PowerState.OFF

    @pytest.mark.anyio
    async def test_unknown_outlet(self, outlets_rig):
        rig = outlets_rig
        with pytest.raises(UnknownDevice):
            await self.connect(rig, rig.outlets("Strip", [("Fan", False)], child="Toaster"))
