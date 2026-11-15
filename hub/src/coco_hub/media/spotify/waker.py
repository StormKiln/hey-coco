"""Bring the iPad back into Spotify's device list by asking the Display (#5).

Spotify drops an idle iPad from ``/me/player/devices``. The only thing that brings it back is
the Spotify app running in the foreground for a moment, and only the iPad can do that. So the
Hub publishes ``spotify.wake_requested`` on the event stream, waits ``spotify.wake_wait_s``,
and lets ``SpotifyService.target_device`` list devices once more.

The Display opens the ``spotify:`` URL scheme **only when Guided Access is off**: under Guided
Access iOS won't switch apps, so the Display ignores the event and Coco ends up saying "Open
Spotify on the iPad once, then try again." after the wait. Even when it works, Spotify stays
in front: iOS gives Coco no way to come back by itself. This is why spike #5 measures how long
the iPad stays listed while Coco holds the screen (``make spotify-spike``).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from coco_hub.events import EventBus
from coco_hub.protocol import SpotifyWakeRequestedEvent

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DisplayWaker:
    """``DeviceWaker`` that asks the connected Display to open the Spotify app."""

    def __init__(
        self,
        bus: EventBus,
        *,
        wait_s: float,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bus = bus
        self._wait_s = wait_s
        self._clock = clock
        self._sleep = sleep

    async def wake(self, device_name: str | None) -> bool:
        """True when a Display was asked (worth listing devices again), else False."""
        if self._wait_s <= 0:
            return False
        if self._bus.subscriber_count == 0:
            log.info("Spotify device missing and no Display connected to wake it")
            return False
        self._bus.publish(
            SpotifyWakeRequestedEvent(server_time=self._clock(), device_name=device_name)
        )
        log.info("asked the Display to open Spotify; waiting %gs", self._wait_s)
        await self._sleep(self._wait_s)
        return True
