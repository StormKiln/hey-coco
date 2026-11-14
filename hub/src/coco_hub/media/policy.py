"""Media policy: the one choke point every media request passes through.

**This is where parental controls / content lockdown go.** Every intent that starts new
media (YouTube now; Spotify music, sounds and pictures later) builds a ``MediaRequest`` and
asks the policy registered at ``app.state.services["media_policy"]`` before it shows or plays
anything. A denial is spoken to the household using ``MediaDecision.reason``.

A request is checked twice for searches:

1. with only ``kind`` and ``query`` (before searching, so a blocked query spends no quota),
2. once per candidate item, with ``item_id`` / ``title`` / ``channel`` filled in; the first
   allowed item is played (``media.next`` checks the following items the same way).

Transport controls (pause / resume / stop / volume) don't start new content and are not
checked.

v1 ships ``AllowAllPolicy`` (the family chose no lockdown for now; see the design doc). To
add controls later, implement ``MediaPolicy.check`` (e.g. channel allow-lists, blocked words,
time-of-day rules) and register it in ``app.py``. Handlers fetch the policy with
``policy_from(services)``, which fails closed: a missing policy is an error, never "allow".
See ``docs/media-policy.md``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

MediaKind = Literal["youtube", "music", "sound", "picture"]

DEFAULT_DENY_SAY = "Sorry, I can't play that."
SERVICE_KEY = "media_policy"


@dataclass(frozen=True)
class MediaRequest:
    kind: MediaKind
    query: str
    """What was asked for, as normalised text (e.g. ``bluey``)."""
    item_id: str | None = None
    """The concrete item (YouTube video id, Spotify URI, ...) once known; None for the
    query-level check."""
    title: str | None = None
    channel: str | None = None
    """YouTube channel / music artist, when known."""


@dataclass(frozen=True)
class MediaDecision:
    allowed: bool
    reason: str | None = None
    """Spoken to the household on a denial; ``DEFAULT_DENY_SAY`` when None."""

    @classmethod
    def allow(cls) -> "MediaDecision":
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str | None = None) -> "MediaDecision":
        return cls(allowed=False, reason=reason)

    @property
    def say(self) -> str:
        return self.reason or DEFAULT_DENY_SAY


class MediaPolicy(Protocol):
    def check(self, request: MediaRequest) -> MediaDecision: ...


class AllowAllPolicy:
    """v1: no lockdown."""

    def check(self, request: MediaRequest) -> MediaDecision:
        return MediaDecision.allow()


def policy_from(services: Mapping[str, Any]) -> MediaPolicy:
    """The registered policy. Raises KeyError if none is registered (fail closed)."""
    return services[SERVICE_KEY]
