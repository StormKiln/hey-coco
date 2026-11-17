"""Brand-neutral device vocabulary: capabilities and state."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class Capability(StrEnum):
    """What a device can do. Intents check capabilities, never brands."""

    POWER = "power"
    BRIGHTNESS = "brightness"
    COLOR = "color"


class PowerState(StrEnum):
    ON = "on"
    OFF = "off"
    MIXED = "mixed"
    """A multi-outlet device addressed as a whole, with some outlets on and some off."""


@dataclass(frozen=True)
class OutletState:
    name: str
    on: bool


@dataclass(frozen=True)
class DeviceState:
    power: PowerState
    outlets: tuple[OutletState, ...] = ()
    """Per-outlet detail when a multi-outlet device is addressed as a whole."""

    @classmethod
    def of(cls, on: bool) -> "DeviceState":
        return cls(PowerState.ON if on else PowerState.OFF)

    @classmethod
    def from_outlets(cls, outlets: Sequence[OutletState]) -> "DeviceState":
        ons = {o.on for o in outlets}
        if ons == {True}:
            power = PowerState.ON
        elif ons == {False} or not ons:
            power = PowerState.OFF
        else:
            power = PowerState.MIXED
        return cls(power, tuple(outlets))
