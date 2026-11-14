"""Hub-authoritative timers, stored in SQLite with absolute UTC end times.

States: ``running`` → ``ringing`` (fired) → ``dismissed``; ``running``/``ringing`` →
``cancelled``. Adding time to a ringing timer restarts it (``running`` again, ending
``seconds`` from now). ``duration_s`` is always ``ends_at - created_at``.

Names are normalised (``"The Pasta timer"`` → ``"pasta"``) and unique among active timers.
An unnamed timer gets the lowest free default name among active timers: ``timer``, then
``timer 2``, ``timer 3``…; a clashing name gets the same suffix (``pasta 2``). So a stored
timer always has a name, though the protocol still allows ``null``.

Firing is done by ``tick()`` (and the ``run()`` loop that calls it):

- a running timer whose ``ends_at`` has passed starts ringing and publishes ``timer.fired``;
  if it ended more than ``missed_grace_s`` ago (the Hub was down through it), it is
  dismissed silently instead — nobody wants a stale pasta alarm hours later. The Display's
  own local alarm covers the Hub being down;
- a timer that has rung for ``ring_cap_s`` is dismissed (``timer.dismissed``) so it doesn't
  ring forever when nobody is home. ``updated_at`` records when ringing started, so a timer
  recovered at startup rings for the full cap from then.
"""

import asyncio
import logging
import re
import secrets
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from coco_hub.events import EventBus
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import Timer, TimerEvent, TimerState

log = logging.getLogger(__name__)

DEFAULT_NAME = "timer"
MAX_SLEEP_S = 30.0
"""The runner re-checks at least this often, so wall-clock jumps (Mac sleep) are caught."""

_LEADING = {"the", "a", "an", "my", "our", "this", "that", "it"}
_TRAILING = {"timer", "timers", "one"}


class TimerNotFound(LookupError):
    pass


class TimerStateError(ValueError):
    """The timer is in a state that doesn't allow the operation."""


def normalize_timer_name(raw: str | None) -> str | None:
    """``"The Pasta timer"`` → ``"pasta"``; ``"the timer"``/empty → ``None``."""
    if raw is None:
        return None
    words = normalize(raw).split()
    while words and words[0] in _LEADING:
        words.pop(0)
    while words and words[-1] in _TRAILING:
        words.pop()
    return " ".join(words) or None


def _singular(name: str) -> str:
    return re.sub(r"(?:es|s)$", "", name)


def _ts(dt: datetime) -> str:
    """Fixed-width UTC text, so SQL string comparison and ordering match time order."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _utc_now() -> datetime:
    return datetime.now(UTC)


EventType = Literal[
    "timer.created", "timer.updated", "timer.fired", "timer.dismissed", "timer.cancelled"
]


class TimerService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] = _utc_now,
        bus: EventBus | None = None,
        ring_cap_s: int = 600,
        missed_grace_s: int = 3600,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._bus = bus
        self.ring_cap_s = ring_cap_s
        self.missed_grace_s = missed_grace_s
        self._wake = asyncio.Event()

    # --- reads ---------------------------------------------------------------------------

    def _rows(self, where: str = "", params: tuple[object, ...] = ()) -> list[Timer]:
        rows = self._conn.execute(
            "SELECT id, name, duration_s, created_at, ends_at, state FROM timers "
            f"{where} ORDER BY ends_at, created_at",
            params,
        ).fetchall()
        return [
            Timer(
                id=r[0],
                name=r[1],
                duration_s=r[2],
                created_at=_dt(r[3]),
                ends_at=_dt(r[4]),
                state=r[5],
            )
            for r in rows
        ]

    def get(self, timer_id: str) -> Timer | None:
        found = self._rows("WHERE id = ?", (timer_id,))
        return found[0] if found else None

    def _require(self, timer_id: str) -> Timer:
        timer = self.get(timer_id)
        if timer is None:
            raise TimerNotFound(timer_id)
        return timer

    def list_active(self) -> list[Timer]:
        """Running and ringing timers, soonest end first."""
        return self._rows("WHERE state IN ('running', 'ringing')")

    def find_by_name(self, name: str | None) -> Timer | None:
        """Active timer with this (normalised) name; plural-insensitive as a fallback."""
        wanted = normalize_timer_name(name)
        if wanted is None:
            return None
        active = self.list_active()
        for timer in active:
            if timer.name == wanted:
                return timer
        for timer in active:
            if timer.name is not None and _singular(timer.name) == _singular(wanted):
                return timer
        return None

    # --- writes --------------------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)

    def _unique_name(self, name: str | None) -> str:
        base = normalize_timer_name(name) or DEFAULT_NAME
        taken = {t.name for t in self.list_active()}
        if base not in taken:
            return base
        n = 2
        while f"{base} {n}" in taken:
            n += 1
        return f"{base} {n}"

    def _publish(self, type_: EventType, timer: Timer, now: datetime) -> None:
        if self._bus is not None:
            self._bus.publish(TimerEvent(type=type_, server_time=now, timer=timer))

    def _set(
        self,
        timer: Timer,
        now: datetime,
        *,
        state: TimerState | None = None,
        ends_at: datetime | None = None,
    ) -> Timer:
        ends = ends_at or timer.ends_at
        updated = timer.model_copy(
            update={
                "state": state or timer.state,
                "ends_at": ends,
                "duration_s": int((ends - timer.created_at).total_seconds()),
            }
        )
        with self._conn:
            self._conn.execute(
                "UPDATE timers SET state = ?, ends_at = ?, duration_s = ?, updated_at = ? "
                "WHERE id = ?",
                (updated.state, _ts(updated.ends_at), updated.duration_s, _ts(now), timer.id),
            )
        return updated

    def create(self, name: str | None, duration_s: int) -> Timer:
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")
        now = self._now()
        created = now.replace(microsecond=0)
        timer = Timer(
            id=f"t_{secrets.token_hex(4)}",
            name=self._unique_name(name),
            duration_s=duration_s,
            created_at=created,
            ends_at=created + timedelta(seconds=duration_s),
            state="running",
        )
        with self._conn:
            self._conn.execute(
                "INSERT INTO timers (id, name, duration_s, created_at, ends_at, state, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    timer.id,
                    timer.name,
                    timer.duration_s,
                    _ts(timer.created_at),
                    _ts(timer.ends_at),
                    timer.state,
                    _ts(now),
                ),
            )
        self._publish("timer.created", timer, now)
        self.wake()
        return timer

    def add_time(self, timer_id: str, seconds: int) -> Timer:
        """Extend a running timer, or restart a ringing one ``seconds`` from now."""
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        timer = self._require(timer_id)
        now = self._now()
        if timer.state == "running":
            ends = timer.ends_at + timedelta(seconds=seconds)
        elif timer.state == "ringing":
            ends = now.replace(microsecond=0) + timedelta(seconds=seconds)
        else:
            raise TimerStateError(f"timer {timer_id} is {timer.state}")
        updated = self._set(timer, now, state="running", ends_at=ends)
        self._publish("timer.updated", updated, now)
        self.wake()
        return updated

    def cancel(self, timer_id: str) -> Timer:
        """Cancel a running or ringing timer. Cancelling a cancelled timer is a no-op."""
        timer = self._require(timer_id)
        if timer.state == "cancelled":
            return timer
        if timer.state == "dismissed":
            raise TimerStateError(f"timer {timer_id} is dismissed")
        now = self._now()
        updated = self._set(timer, now, state="cancelled")
        self._publish("timer.cancelled", updated, now)
        self.wake()
        return updated

    def cancel_all(self) -> list[Timer]:
        return [self.cancel(t.id) for t in self.list_active()]

    def dismiss(self, timer_id: str) -> Timer:
        """Dismiss a ringing timer. Dismissing a dismissed timer is a no-op."""
        timer = self._require(timer_id)
        if timer.state == "dismissed":
            return timer
        if timer.state != "ringing":
            raise TimerStateError(f"timer {timer_id} is {timer.state}, not ringing")
        now = self._now()
        updated = self._set(timer, now, state="dismissed")
        self._publish("timer.dismissed", updated, now)
        self.wake()
        return updated

    def dismiss_ringing(self) -> list[Timer]:
        return [self.dismiss(t.id) for t in self.list_active() if t.state == "ringing"]

    # --- firing --------------------------------------------------------------------------

    def tick(self) -> datetime | None:
        """Fire due timers and expire long-ringing ones. Returns when to tick next."""
        now = self._now()
        grace = timedelta(seconds=self.missed_grace_s)
        cap = timedelta(seconds=self.ring_cap_s)
        for timer in self._rows("WHERE state = 'running' AND ends_at <= ?", (_ts(now),)):
            if now - timer.ends_at > grace:
                updated = self._set(timer, now, state="dismissed")
                self._publish("timer.dismissed", updated, now)
            else:
                updated = self._set(timer, now, state="ringing")
                self._publish("timer.fired", updated, now)

        next_due: datetime | None = None
        rows = self._conn.execute(
            "SELECT id, state, ends_at, updated_at FROM timers "
            "WHERE state IN ('running', 'ringing')"
        ).fetchall()
        for timer_id, state, ends_at, updated_at in rows:
            if state == "running":
                due = _dt(ends_at)
            else:
                due = _dt(updated_at) + cap
                if due <= now:
                    timer = self._require(timer_id)
                    updated = self._set(timer, now, state="dismissed")
                    self._publish("timer.dismissed", updated, now)
                    continue
            next_due = due if next_due is None else min(next_due, due)
        return next_due

    def wake(self) -> None:
        """Make ``run()`` re-check now (timers changed, or the clock moved in tests)."""
        self._wake.set()

    async def run(self) -> None:
        """Tick forever, sleeping until the next due time or until woken."""
        while True:
            self._wake.clear()
            try:
                next_due = self.tick()
            except Exception:
                log.exception("timer tick failed")
                next_due = None
            delay = MAX_SLEEP_S
            if next_due is not None:
                delay = min(max((next_due - self._now()).total_seconds(), 0.0), MAX_SLEEP_S)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass
