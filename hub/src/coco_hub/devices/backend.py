"""The interface every device backend implements (Kasa now; Shelly, Hue, Matter, ... later).

Adding a backend: write ``devices/backends/<name>.py`` with a ``DeviceBackend`` subclass, list
it in ``devices.backends.BACKENDS``, and make it pass ``tests/devices/backend_contract.py``.
Intents, the config schema and the Hub <-> Display protocol stay as they are.

Backends raise only the typed errors in ``devices.errors``. The ``DeviceService`` adds the
per-call timeout, so a backend needn't (but may) enforce its own.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from coco_hub.devices.errors import Unsupported
from coco_hub.devices.model import Capability, DeviceState
from coco_hub.settings import BackendSettings


class DeviceHandle(ABC):
    """A connected device, or one outlet of a multi-outlet device."""

    alias: str
    """The device's own name (as set in its app): the spoken name when config has none."""
    capabilities: frozenset[Capability]

    @abstractmethod
    async def get_state(self) -> DeviceState:
        """Read the state from the device now (never a cached value)."""

    async def set_power(self, on: bool) -> None:
        """Switch on/off. Callers read the state back to confirm."""
        raise Unsupported(f"{self.alias} has no power switch")

    async def close(self) -> None:  # noqa: B027 - optional hook, idempotent
        """Release connections. Must be safe to call twice."""


class DeviceBackend(ABC):
    name: ClassVar[str]

    def __init__(self, settings: BackendSettings | None = None, **_: Any) -> None:
        self.settings = settings or BackendSettings()

    @abstractmethod
    def parse_options(self, options: Mapping[str, Any]) -> Any:
        """Validate a ``[[devices]]`` entry's ``options`` (raise ``ValueError``, which includes
        pydantic's ``ValidationError``). Called at startup so bad config fails loudly."""

    @abstractmethod
    async def connect(self, options: Any) -> DeviceHandle:
        """Find and connect to the device ``options`` (from ``parse_options``) address."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release backend-wide resources."""
