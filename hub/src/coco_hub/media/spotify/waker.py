"""Bring the iPad back into Spotify's device list by asking the Display (#5).

Spotify drops an idle iPad from ``/me/player/devices``. The only thing that brings it back is
the Spotify app running in the foreground for a moment, and only the iPad can do that. So the
Hub publishes ``spotify.wake_requested`` on the event stream, then lists devices every
``spotify.wake_poll_s`` (1 s) until the iPad is back or ``spotify.wake_wait_s`` (12 s) has
passed (#95: one fixed 4 s wait missed an iPad that reappeared about 6 s after the wake).

The Display opens the ``spotify:`` URL scheme **only when Guided Access is off**: under Guided
Access iOS won't switch apps, so the Display ignores the event and Coco ends up saying "Open
Spotify on the iPad once, then try again." after the whole wait. Even when it works, Spotify stays
in front: iOS gives Coco no way to come back by itself. This is why spike #5 measures how long
the iPad stays listed while Coco holds the screen (``make spotify-spike``).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

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
        poll_s: float = 1.0,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bus = bus
        self._wait_s = wait_s
        self._poll_s = poll_s
        self._clock = clock
        self._sleep = sleep

    async def wake(self, device_name: str | None, appeared: Callable[[], Awaitable[bool]]) -> bool:
        """Ask the Display to open Spotify, then call ``appeared`` (list devices) every
        ``poll_s`` until it returns True (-> True) or ``wait_s`` has passed (-> False).

        The wait ends at whichever comes first: ``wait_s`` of sleeps, or ``wait_s`` on the
        clock (device listings take time too).
        """
        if self._wait_s <= 0:
            return False
        if self._bus.subscriber_count == 0:
            log.info("Spotify device missing and no Display connected to wake it")
            return False
        started = self._clock()
        self._bus.publish(SpotifyWakeRequestedEvent(server_time=started, device_name=device_name))
        log.info("asked the Display to open Spotify; polling for up to %gs", self._wait_s)
        deadline = started + timedelta(seconds=self._wait_s)
        slept = 0.0
        while (left := min(self._wait_s - slept, (deadline - self._clock()).total_seconds())) > 0:
            step = min(self._poll_s, left)
            await self._sleep(step)
            slept += step
            if await appeared():
                elapsed = (self._clock() - started).total_seconds()
                log.info("Spotify device back after %.1fs", elapsed)
                return True
        log.info("Spotify device still missing after %gs", self._wait_s)
        return False
